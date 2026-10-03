import customtkinter as ctk
from tkinter import messagebox, simpledialog, Toplevel, StringVar
import requests
import threading
import time
import webbrowser
import uuid
import sys
import os
import io
from mss import mss
import subprocess
from urllib.parse import urlparse
import socket
import atexit

def get_server_ip(url):
    try:
        hostname = urlparse(url).hostname
        if not hostname: return None
        return socket.gethostbyname(hostname)
    except:
        return None

def setup_firewall_rules(server_url):
    try:
        server_ip = get_server_ip(server_url)
        if not server_ip or server_ip in ('127.0.0.1', 'localhost'): return
        
        pf_rules = f"""
pass out quick proto tcp from any to {server_ip} port 80
pass out quick proto tcp from any to {server_ip} port 443
block drop out quick proto tcp from any to any port 80
block drop out quick proto tcp from any to any port 443
"""
        with open("/tmp/exam_pf.conf", "w") as f:
            f.write(pf_rules)
        
        cmd = "cat /etc/pf.conf /tmp/exam_pf.conf > /tmp/exam_pf_combined.conf && pfctl -f /tmp/exam_pf_combined.conf && pfctl -e && touch /tmp/exam_client_pf_active"
        script = f'do shell script "{cmd}" with administrator privileges'
        res = subprocess.run(['osascript', '-e', script], stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
        
        if res.returncode != 0:
            import tkinter as tk
            from tkinter import simpledialog
            pwd = simpledialog.askstring("Yêu cầu mật khẩu", "Không thể thiết lập tường lửa do thiếu quyền Admin.\nVui lòng nhập mật khẩu từ giảng viên để tiếp tục với cơ chế dự phòng:", show="*")
            if pwd == "123456":
                messagebox.showwarning("Cảnh báo", "Tính năng 'Cắt mạng hoàn toàn' sẽ không hoạt động. Phần mềm sẽ tiếp tục với cơ chế dự phòng (giám sát cửa sổ).")
            else:
                messagebox.showerror("Lỗi", "Sai mật khẩu! Không thể vào phòng thi.")
                raise Exception("Bypass password incorrect")
        else:
            print("Firewall rules applied on macOS.")
    except Exception as e:
        print(f"Firewall setup error: {e}")
        raise e

def remove_firewall_rules():
    try:
        if os.path.exists("/tmp/exam_client_pf_active"):
            cmd = "pfctl -f /etc/pf.conf && rm -f /tmp/exam_client_pf_active"
            script = f'do shell script "{cmd}" with administrator privileges'
            subprocess.run(['osascript', '-e', script], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            print("Firewall rules removed on macOS.")
    except Exception as e:
        print(f"Firewall removal error: {e}")

# Đăng ký atexit để luôn xoá rules khi app thoát
atexit.register(remove_firewall_rules)

# Dọn dẹp rule bị sót từ lần trước
remove_firewall_rules()

# =============================================================================
# --- CẤU HÌNH GIAO DIỆN CHUYÊN NGHIỆP ---
# =============================================================================
ctk.set_appearance_mode("Light") # Chế độ sáng

# Bảng màu Google Material Design
COLOR_PRIMARY = "#1A73E8"
COLOR_PRIMARY_HOVER = "#1B66C9"
COLOR_SURFACE = "#FFFFFF" # Màu nền của các frame con
COLOR_BACKGROUND = "#F1F3F4" # Màu nền chính của cửa sổ
COLOR_TEXT_PRIMARY = "#202124"
COLOR_TEXT_SECONDARY = "#5F6368"
COLOR_BORDER = "#DADCE0"

# Định nghĩa Font
FONT_TITLE = ("Roboto", 24, "bold")
FONT_SUBTITLE = ("Roboto", 14)
FONT_BODY = ("Roboto", 13)
FONT_BUTTON = ("Roboto", 14, "bold")

# --- Logic Cấu hình Nâng cao (Giữ nguyên) ---
def prompt_for_server_url_on_startup():
    root = ctk.CTk()
    root.withdraw()
    url = simpledialog.askstring("Kết nối đến Server",
                                 "Vui lòng nhập địa chỉ Server do Giảng viên cung cấp:",
                                 parent=root)
    root.destroy()
    return url.strip() if url and url.strip() else None

# --- Khởi tạo ứng dụng ---
SERVER_URL = prompt_for_server_url_on_startup()
HEARTBEAT_INTERVAL = 15
SCREENSHOT_INTERVAL = 5

# --- Biến toàn cục ---
stop_heartbeat = threading.Event()
auth_token = None
device_id = ":".join(['{:02x}'.format((uuid.getnode() >> i) & 0xff) for i in range(0, 8 * 6, 8)][::-1])
app_instance = None
banned_sites_list = []
unlock_pwd = "123"
is_ui_locked = False

# =============================================================================
# --- LỚP ỨNG DỤNG CHÍNH ---
# =============================================================================
class App(ctk.CTk):
    def __init__(self):
        super().__init__()
        if not SERVER_URL:
            self.destroy(); return

        self.title("IUH Exam Client")
        self.geometry("400x600")
        self.resizable(False, False)
        self.configure(fg_color=COLOR_BACKGROUND)

        self.container = ctk.CTkFrame(self, fg_color="transparent")
        self.container.pack(fill="both", expand=True)
        self.container.grid_rowconfigure(0, weight=1)
        self.container.grid_columnconfigure(0, weight=1)

        self.frames = {}
        for F in (LoginFrame, RegisterFrame, ExamCodeFrame, ActiveExamFrame):
            frame = F(self.container, self)
            self.frames[F.__name__] = frame
            frame.grid(row=0, column=0, sticky="nsew")
        
        self.show_frame("LoginFrame")
        self.protocol("WM_DELETE_WINDOW", self.on_closing)

    def show_frame(self, page_name):
        self.frames[page_name].tkraise()

    def on_closing(self):
        if messagebox.askokcancel("Xác nhận thoát", "Bạn có chắc muốn thoát ứng dụng Client không?\n\nHành động này sẽ dừng gửi tín hiệu kết nối và bạn sẽ bị ghi nhận là đã ngắt kết nối."):
            if auth_token and SERVER_URL:
                try:
                    requests.post(f"{SERVER_URL}/api/service/leave", headers={"Authorization": f"Bearer {auth_token}"}, timeout=2)
                except: pass
            stop_heartbeat.set()
            remove_firewall_rules()
            self.destroy()
            sys.exit(0)

# =============================================================================
# --- FRAME ĐĂNG NHẬP ---
# =============================================================================
class LoginFrame(ctk.CTkFrame):
    def __init__(self, parent, controller):
        super().__init__(parent, fg_color=COLOR_BACKGROUND)
        
        main_frame = ctk.CTkFrame(self, width=320, height=450, fg_color=COLOR_SURFACE, corner_radius=10)
        main_frame.pack(expand=True)
        main_frame.pack_propagate(False)

        ctk.CTkLabel(main_frame, text="🔑", font=("Segoe UI Emoji", 30)).pack(pady=(30, 0))
        
        ctk.CTkLabel(main_frame, text="Đăng nhập", font=FONT_TITLE).pack(pady=(10, 8))
        ctk.CTkLabel(main_frame, text="Chào mừng trở lại!", font=FONT_SUBTITLE, text_color=COLOR_TEXT_SECONDARY).pack(pady=(0, 30))
        
        ctk.CTkEntry(main_frame, placeholder_text="Email", width=280, height=40, border_color=COLOR_BORDER, font=FONT_BODY).pack(pady=(0, 15))
        self.email_entry = main_frame.winfo_children()[-1]
        
        ctk.CTkEntry(main_frame, show="*", placeholder_text="Mật khẩu", width=280, height=40, border_color=COLOR_BORDER, font=FONT_BODY).pack(pady=(0, 20))
        self.password_entry = main_frame.winfo_children()[-1]
        
        ctk.CTkButton(main_frame, text="Đăng nhập", width=280, height=40, font=FONT_BUTTON, command=self.handle_login).pack(pady=(10, 10))
        ctk.CTkButton(main_frame, text="Chưa có tài khoản? Đăng ký", fg_color="transparent", text_color=COLOR_PRIMARY, hover=False, font=FONT_BODY, command=lambda: controller.show_frame("RegisterFrame")).pack()

    def handle_login(self):
        email, password = self.email_entry.get().strip(), self.password_entry.get().strip()
        if not email or not password: return messagebox.showerror("Lỗi", "Vui lòng nhập đủ email và mật khẩu.")
        try:
            res = requests.post(f"{SERVER_URL}/api/service/login", json={"email": email, "password": password})
            data = res.json()
            if res.ok and data.get("ok"):
                global auth_token; auth_token = data.get("auth_token")
                self.winfo_toplevel().show_frame("ExamCodeFrame")
                start_background_threads()
            else: messagebox.showerror("Lỗi", data.get("error", "Đăng nhập thất bại."))
        except requests.RequestException: messagebox.showerror("Lỗi mạng", f"Không thể kết nối đến server tại:\n{SERVER_URL}")

# =============================================================================
# --- FRAME ĐĂNG KÝ ---
# =============================================================================
class RegisterFrame(ctk.CTkFrame):
    def __init__(self, parent, controller):
        super().__init__(parent, fg_color=COLOR_BACKGROUND)
        
        main_frame = ctk.CTkFrame(self, width=320, height=520, fg_color=COLOR_SURFACE, corner_radius=10)
        main_frame.pack(expand=True)
        main_frame.pack_propagate(False)
        
        ctk.CTkLabel(main_frame, text="📝", font=("Segoe UI Emoji", 30)).pack(pady=(20, 0))
        ctk.CTkLabel(main_frame, text="Tạo tài khoản", font=FONT_TITLE).pack(pady=(10, 20))
        
        self.entries = {}
        fields = {"Họ và tên": "ho_ten", "MSSV": "mssv", "Lớp": "lop", "Email": "email", "Mật khẩu": "password"}
        
        for label, key in fields.items():
            entry = ctk.CTkEntry(main_frame, placeholder_text=label, width=280, height=40, border_color=COLOR_BORDER, font=FONT_BODY, show="*" if key == "password" else None)
            entry.pack(pady=(0, 15))
            self.entries[key] = entry
            
        ctk.CTkButton(main_frame, text="Đăng ký", width=280, height=40, font=FONT_BUTTON, command=self.handle_register).pack(pady=(10, 10))
        ctk.CTkButton(main_frame, text="Đã có tài khoản? Đăng nhập", fg_color="transparent", text_color=COLOR_PRIMARY, hover=False, font=FONT_BODY, command=lambda: controller.show_frame("LoginFrame")).pack()

    def handle_register(self):
        payload = {key: entry.get().strip() for key, entry in self.entries.items()}
        if not all(payload.values()): return messagebox.showerror("Lỗi", "Vui lòng nhập đầy đủ thông tin.")
        try:
            res = requests.post(f"{SERVER_URL}/api/service/register", json=payload)
            data = res.json()
            if res.ok and data.get("ok"):
                messagebox.showinfo("Thành công", "Đăng ký thành công! Vui lòng quay lại trang đăng nhập.")
                self.winfo_toplevel().show_frame("LoginFrame")
            else: messagebox.showerror("Lỗi", data.get("error", "Đăng ký thất bại."))
        except requests.RequestException: messagebox.showerror("Lỗi mạng", f"Không thể kết nối đến server tại:\n{SERVER_URL}")

# =============================================================================
# --- FRAME VÀO PHÒNG THI ---
# =============================================================================
class ExamCodeFrame(ctk.CTkFrame):
    def __init__(self, parent, controller):
        super().__init__(parent, fg_color=COLOR_BACKGROUND)

        main_frame = ctk.CTkFrame(self, width=320, height=400, fg_color=COLOR_SURFACE, corner_radius=10)
        main_frame.pack(expand=True)
        main_frame.pack_propagate(False)
        
        ctk.CTkLabel(main_frame, text="🧪", font=("Segoe UI Emoji", 30)).pack(pady=(50, 0))
        ctk.CTkLabel(main_frame, text="Vào phòng thi", font=FONT_TITLE).pack(pady=(10, 8))
        ctk.CTkLabel(main_frame, text="Vui lòng nhập mã do giám thị cung cấp.", font=FONT_SUBTITLE, text_color=COLOR_TEXT_SECONDARY).pack(pady=(0, 40))
        
        self.exam_code_entry = ctk.CTkEntry(main_frame, placeholder_text="MÃ PHÒNG THI", width=280, height=50, border_color=COLOR_BORDER, font=ctk.CTkFont(size=16, weight="bold"), justify="center")
        self.exam_code_entry.pack(pady=(0, 20))

        ctk.CTkButton(main_frame, text="Vào phòng", width=280, height=40, font=FONT_BUTTON, command=self.handle_join_exam).pack(pady=(10, 10))

    def handle_join_exam(self):
        exam_code = self.exam_code_entry.get().strip()
        if not exam_code: return messagebox.showerror("Lỗi", "Vui lòng nhập mã phòng thi.")
        try:
            res = requests.post(f"{SERVER_URL}/api/service/join_exam", headers={"Authorization": f"Bearer {auth_token}"}, json={"exam_code": exam_code})
            data = res.json()
            if res.ok and data.get("ok"):
                global banned_sites_list, unlock_pwd
                if "banned_sites" in data:
                    sites_str = data.get("banned_sites") or ""
                    banned_sites_list = [s.strip().lower() for s in sites_str.split(",") if s.strip()]
                    unlock_pwd = data.get("unlock_password") or "123"
                    
                try:
                    setup_firewall_rules(SERVER_URL)
                except Exception as e:
                    return # Stop joining exam if firewall rules and bypass failed

                webbrowser.open(f"{SERVER_URL}/exam/start_session?token={data.get('one_time_token')}")
                self.winfo_toplevel().show_frame("ActiveExamFrame")
            else: messagebox.showerror("Lỗi", data.get("error", "Vào phòng thất bại."))
        except requests.RequestException: messagebox.showerror("Lỗi mạng", f"Không thể kết nối đến server tại:\n{SERVER_URL}")

# =============================================================================
# --- FRAME ĐANG THI ---
# =============================================================================
class ActiveExamFrame(ctk.CTkFrame):
    def __init__(self, parent, controller):
        super().__init__(parent, fg_color=COLOR_BACKGROUND)

        main_frame = ctk.CTkFrame(self, width=320, height=350, fg_color=COLOR_SURFACE, corner_radius=10)
        main_frame.pack(expand=True)
        main_frame.pack_propagate(False)
        
        ctk.CTkLabel(main_frame, text="✅", font=("Segoe UI Emoji", 40)).pack(pady=(40, 10))
        ctk.CTkLabel(main_frame, text="Đang trong phòng thi", font=FONT_TITLE).pack(pady=(0, 10))
        ctk.CTkLabel(main_frame, text="Vui lòng giữ ứng dụng này mở\ntrong suốt quá trình làm bài.", font=FONT_SUBTITLE, text_color=COLOR_TEXT_SECONDARY, justify="center").pack(pady=(0, 30))

        ctk.CTkButton(main_frame, text="Hoàn thành thi & Thoát", width=280, height=45, font=FONT_BUTTON, fg_color="#D32F2F", hover_color="#B71C1C", command=controller.on_closing).pack(pady=(10, 10))

# =============================================================================
# --- LOGIC CHẠY NỀN ---
# =============================================================================
def send_heartbeat_loop():
    while not stop_heartbeat.is_set():
        try:
            res = requests.post(f"{SERVER_URL}/api/service/ping", headers={"Authorization": f"Bearer {auth_token}"}, json={"device_id": device_id})
            
            if res.status_code == 403: # 403 Forbidden
                messagebox.showerror("Phiên kết thúc", "Bạn đã bị giám thị cấm thi hoặc phòng thi đã đóng. Ứng dụng sẽ tự động thoát.")
                remove_firewall_rules()
                stop_heartbeat.set()
                break

            if res.status_code == 401: # 401 Unauthorized
                messagebox.showerror("Phiên hết hạn", "Phiên đăng nhập đã hết hạn. Vui lòng đăng nhập lại.")
                stop_heartbeat.set()
                break
                
            if res.ok:
                data = res.json()
                if "banned_sites" in data:
                    global banned_sites_list, unlock_pwd
                    sites_str = data.get("banned_sites") or ""
                    banned_sites_list = [s.strip().lower() for s in sites_str.split(",") if s.strip()]
                    unlock_pwd = data.get("unlock_password") or "123"
        except Exception as e:
            pass
        
        for _ in range(HEARTBEAT_INTERVAL):
            if stop_heartbeat.is_set(): break
            time.sleep(1)
            
    if app_instance: 
        app_instance.after(100, app_instance.destroy)

def send_screenshot_loop():
    with mss() as sct:
        while not stop_heartbeat.is_set():
            try:
                # Nếu có nhiều màn hình, mss lấy màn 1 làm chính, trên macOS monitor[1] thường là màn hình chính
                monitor_idx = 1 if len(sct.monitors) > 1 else 0
                sct_img = sct.grab(sct.monitors[monitor_idx])
                
                img_bytes = io.BytesIO()
                from PIL import Image
                img = Image.frombytes("RGB", sct_img.size, sct_img.bgra, "raw", "BGRX")
                img.save(img_bytes, format="JPEG", quality=75)
                img_bytes.seek(0)

                files = {'screenshot': ('screenshot.jpg', img_bytes, 'image/jpeg')}
                res = requests.post(
                    f"{SERVER_URL}/api/service/screenshot", 
                    headers={"Authorization": f"Bearer {auth_token}"}, 
                    files=files,
                    timeout=10
                )
            except Exception as e:
                pass
            
            for _ in range(SCREENSHOT_INTERVAL):
                if stop_heartbeat.is_set(): break
                time.sleep(1)

def block_copy_paste_loop():
    while not stop_heartbeat.is_set():
        if not is_ui_locked:
            try:
                # Xóa clipboard qua Tkinter cho MacOS
                if app_instance:
                    app_instance.clipboard_clear()
            except:
                pass
        time.sleep(0.1)

def show_lock_screen():
    global is_ui_locked
    if is_ui_locked: return
    is_ui_locked = True
    
    lock_win = Toplevel(app_instance)
    lock_win.attributes("-fullscreen", True)
    lock_win.configure(bg="white")
    lock_win.protocol("WM_DELETE_WINDOW", lambda: None)
    
    ctk.CTkLabel(lock_win, text="🔒", font=("Segoe UI Emoji", 100), text_color="red", bg_color="white").pack(pady=(200, 20))
    ctk.CTkLabel(lock_win, text="Bạn đã vi phạm quy chế thi vì sử dụng trang web không được phép", font=("Roboto", 40, "bold"), text_color="red", bg_color="white").pack(pady=10)
    
    pwd_var = StringVar()
    pwd_entry = ctk.CTkEntry(lock_win, textvariable=pwd_var, show="*", width=200, bg_color="white", fg_color="white", text_color="black")
    
    def check_pwd(event=None):
        if pwd_var.get() == unlock_pwd:
            global is_ui_locked
            is_ui_locked = False
            lock_win.destroy()
            
            if auth_token and SERVER_URL:
                try:
                    requests.post(f"{SERVER_URL}/api/service/leave", headers={"Authorization": f"Bearer {auth_token}"}, timeout=2)
                except: pass
            stop_heartbeat.set()
            remove_firewall_rules()
            os._exit(0)
        else:
            messagebox.showerror("Sai mật khẩu", "Mật khẩu không đúng!")
            pwd_var.set("")
            
    pwd_entry.bind("<Return>", check_pwd)
    
    def on_shortcut(event):
        pwd_entry.pack(pady=20)
        pwd_entry.focus_set()
        
    lock_win.bind("<Command-D>", on_shortcut)
    lock_win.bind("<Command-Shift-d>", on_shortcut)
    
    # Report violation
    if auth_token and SERVER_URL:
        try:
            requests.post(f"{SERVER_URL}/api/service/violation", headers={"Authorization": f"Bearer {auth_token}"}, timeout=2)
        except: pass

    # Khởi động luồng phát âm thanh cảnh báo trên MacOS
    def beep_loop():
        for _ in range(5):
            try:
                subprocess.run(["osascript", "-e", "set volume output volume 100"])
            except:
                pass
        while is_ui_locked:
            try:
                subprocess.run(["afplay", "/System/Library/Sounds/Ping.aiff"])
            except:
                pass
            time.sleep(0.2)

    threading.Thread(target=beep_loop, daemon=True).start()

def monitor_banned_sites_loop():
    while not stop_heartbeat.is_set():
        if not is_ui_locked and banned_sites_list:
            try:
                script = 'tell application "System Events" to get name of every window of (every process whose visible is true)'
                res = subprocess.run(['osascript', '-e', script], capture_output=True, text=True)
                if res.stdout:
                    titles = [t.strip() for t in res.stdout.replace('missing value', '').split(', ') if t.strip()]
                    
                    for title in titles:
                        title_lower = title.lower()
                        for site in banned_sites_list:
                            if site in title_lower:
                                app_instance.after(0, show_lock_screen)
                                time.sleep(5)
                                break
            except Exception as e:
                pass
        time.sleep(2)

background_threads_started = False
def start_background_threads():
    global background_threads_started
    if background_threads_started: return
    background_threads_started = True
    threading.Thread(target=send_heartbeat_loop, daemon=True).start()
    threading.Thread(target=send_screenshot_loop, daemon=True).start()
    threading.Thread(target=block_copy_paste_loop, daemon=True).start()
    threading.Thread(target=monitor_banned_sites_loop, daemon=True).start()

if __name__ == "__main__":
    if SERVER_URL:
        app_instance = App()
        app_instance.mainloop()