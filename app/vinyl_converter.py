import tkinter as tk
import tkinter.filedialog as filedialog
import tkinter.messagebox as messagebox
import customtkinter
import sounddevice as sd
import soundfile as sf
import numpy as np
import threading
import collections
import subprocess
import shutil
import os
import sys

# --- VINTAGE / ANALOG COLORS ---
COLOR_BG         = "#121212"   # Chassi escuro
COLOR_SURFACE    = "#1E1E1E"   # Painéis
COLOR_SURFACE2   = "#282828"   # Botões e fundos de input
COLOR_ACCENT     = "#D87D00"   # Laranja/Âmbar (vintage)
COLOR_TEXT       = "#FFB84D"   # Texto luminoso estilo painel analógico
COLOR_TEXT2      = "#A37A3E"   # Texto secundário dim
COLOR_TEXT3      = "#59472B"   # Texto desabilitado
COLOR_RED        = "#E63900"   # Clip do VU / Gravação
COLOR_YELLOW     = "#FFB300"   # Médio do VU
COLOR_GREEN      = "#85C733"   # Baixo do VU

FONT_FAMILY = "Consolas" if sys.platform == "win32" else "Menlo"
FONT_MAIN = (FONT_FAMILY, 12)
FONT_BOLD = (FONT_FAMILY, 12, "bold")
FONT_TITLE = (FONT_FAMILY, 18, "bold")

class AudioEngine:
    def __init__(self):
        self.input_device = None
        self.output_device = None
        self.sample_rate = 44100
        self.channels = 2
        self.blocksize = 1024
        self.recording = False
        self.paused = False
        self.stream = None
        self.writer = None
        self.output_path = None
        self.monitor_volume = 1.0
        self.input_gain = 1.0
        self.silence_frames = 0
        self.trigger_auto_stop = False
        
        self.waveform_buffer = collections.deque(maxlen=400)
        self.elapsed_seconds = 0.0
        self.on_level_update = None
        self.on_time_update = None

    def get_devices(self) -> dict:
        try:
            sd._terminate()
            sd._initialize()
        except:
            pass
        devices = sd.query_devices()
        apis = sd.query_hostapis()
        
        inputs, outputs = {}, {}
        for i, dev in enumerate(devices):
            api_name = apis[dev['hostapi']]['name'].replace("Windows ", "")
            # Limitar apenas a MME para evitar o crash de "Bloco Sólido" ao misturar APIs
            if "MME" not in api_name:
                continue
                
            name = dev['name']
            if "Mixagem" in name or "Stereo Mix" in name: continue
            
            display_name = name
            if "Mapeador" in name or "Mapper" in name:
                display_name = "🖥️ Áudio Padrão do Windows"
            elif "USB Audio" in name or "Burr-Brown" in name or "Microphone (USB" in name:
                display_name = "🎧 TOCA-DISCOS ION"

            if dev['max_input_channels'] >= 1 and display_name not in inputs:
                inputs[display_name] = {"index": i, "name": display_name}
            if dev['max_output_channels'] >= 1 and display_name not in outputs:
                outputs[display_name] = {"index": i, "name": display_name}
                
        return {"inputs": list(inputs.values()), "outputs": list(outputs.values())}

    def start_stream(self):
        if self.input_device is None or self.output_device is None: return
        try:
            self.stream = sd.Stream(
                device=(self.input_device, self.output_device), samplerate=self.sample_rate,
                channels=self.channels, dtype='float32', blocksize=self.blocksize,
                callback=self._audio_callback, latency='low'
            )
            self.stream.start()
        except: self.stream = None

    def _audio_callback(self, indata, outdata, frames, time_info, status):
        if self.paused:
            outdata[:] = np.zeros_like(outdata)
            return
            
        indata = indata * self.input_gain
        outdata[:] = indata * self.monitor_volume
        
        amplitude_chunk = float(np.max(np.abs(indata)))
        if getattr(self, 'magic_wait', False) and amplitude_chunk > 0.02:
            self.magic_wait = False
            self.start_recording(self.magic_path)
            if hasattr(self, 'on_magic_trigger'): self.on_magic_trigger()
            
        if self.recording and self.writer is not None:
            self.writer.write(indata)
            self.elapsed_seconds += frames / self.sample_rate
            if hasattr(self, 'on_time_update') and self.on_time_update: self.on_time_update(self.elapsed_seconds)
            
            if amplitude_chunk < 0.02:
                self.silence_frames += frames
                if self.silence_frames / self.sample_rate >= 30.0:
                    self.trigger_auto_stop = True
            else:
                self.silence_frames = 0
                
        if indata.shape[1] > 0:
            in_vu_L = float(np.sqrt(np.mean(indata[:, 0]**2)))
            in_vu_R = float(np.sqrt(np.mean(indata[:, 1]**2))) if indata.shape[1] > 1 else in_vu_L
            out_vu_L = float(np.sqrt(np.mean(outdata[:, 0]**2)))
            out_vu_R = float(np.sqrt(np.mean(outdata[:, 1]**2))) if outdata.shape[1] > 1 else out_vu_L
        else:
            in_vu_L, in_vu_R, out_vu_L, out_vu_R = 0.0, 0.0, 0.0, 0.0
            
        amplitude_chunk = float(np.max(np.abs(indata)))
        self.waveform_buffer.append(amplitude_chunk)
        
        # Guardar valores para o loop da UI ler, sem travar o tkinter!
        self.latest_levels = (in_vu_L, in_vu_R, out_vu_L, out_vu_R)
        self.latest_amp = amplitude_chunk

    def start_recording(self, output_path: str):
        self.output_path = output_path
        self.elapsed_seconds = 0.0
        self.writer = sf.SoundFile(
            output_path, mode='w', samplerate=self.sample_rate, channels=self.channels, format='WAV', subtype='PCM_16'
        )
        self.recording = True

    def stop_recording(self) -> str:
        self.recording = False
        if self.writer:
            self.writer.close()
            self.writer = None
        return self.output_path

    def pause(self): self.paused = True
    def resume(self): self.paused = False
    def stop_stream(self):
        if self.stream is not None:
            try:
                self.stream.abort()
                self.stream.close()
            except:
                pass
            self.stream = None

    def export_chunk(self, wav_path: str, out_path: str, metadata: dict, start_time: float, duration: float, fmt: str, denoise: bool, normalize: bool, cover_path: str = None):
        cmd = ["ffmpeg", "-y", "-i", wav_path]
        
        has_cover = cover_path and os.path.exists(cover_path)
        if has_cover:
            cmd.extend(["-i", cover_path])
            
        cmd.extend(["-ss", str(start_time), "-t", str(duration)])
        
        audio_filters = []
        if denoise:
            audio_filters.append("afftdn=nf=-25")
        if normalize:
            # loudnorm = EBU R128 normalization
            audio_filters.append("loudnorm=I=-14:LRA=11:TP=-1.0")
            
        if audio_filters:
            cmd.extend(["-af", ",".join(audio_filters)])
            
        if "MP3" in fmt:
            cmd.extend(["-codec:a", "libmp3lame", "-qscale:a", "2"])
            if has_cover:
                cmd.extend(["-map", "0:a", "-map", "1:v", "-c:v", "mjpeg", "-id3v2_version", "3", "-metadata:s:v", 'title="Album cover"', "-metadata:s:v", 'comment="Cover (front)"'])
        elif "FLAC" in fmt:
            cmd.extend(["-codec:a", "flac"])
            if has_cover:
                cmd.extend(["-map", "0:a", "-map", "1:v", "-c:v", "copy", "-disposition:v", "attached_pic"])
        else:
            cmd.extend(["-codec:a", "pcm_s16le"]) # WAV não suporta capa embutida padrão
            
        if metadata.get("title"): cmd.extend(["-metadata", f"title={metadata['title']}"])
        if metadata.get("artist"): cmd.extend(["-metadata", f"artist={metadata['artist']}"])
        if metadata.get("album"): cmd.extend(["-metadata", f"album={metadata['album']}"])
        if metadata.get("year"): cmd.extend(["-metadata", f"date={metadata['year']}"])
        if metadata.get("track"): cmd.extend(["-metadata", f"track={metadata['track']}"])
        hardware_info = []
        if metadata.get("cartridge"): hardware_info.append(f"Agulha: {metadata['cartridge']}")
        if metadata.get("vinyl_spec"): hardware_info.append(f"Vinil: {metadata['vinyl_spec']}")
        if hardware_info: cmd.extend(["-metadata", f"comment={' | '.join(hardware_info)}"])
        cmd.append(out_path)
        subprocess.run(cmd, check=True, creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)

# --- GUI WIDGETS ---
class RetroWaveform(tk.Canvas):
    def __init__(self, master, **kwargs):
        super().__init__(master, bg=COLOR_BG, highlightthickness=1, highlightbackground=COLOR_TEXT3, width=400, height=120, **kwargs)
        self.history = collections.deque(maxlen=400)
    def add_sample(self, amplitude: float):
        self.history.append(amplitude)
    def redraw(self):
        self.delete("all")
        w = self.winfo_width()
        h = self.winfo_height()
        if w < 10 or h < 10: return
        
        mid_y = h / 2
        self.create_line(0, mid_y, w, mid_y, fill=COLOR_TEXT3, width=1, dash=(2, 4))
        
        if not self.history: return
        step = w / 400.0 # Mantém 400 amostras, mas estica no eixo X
        
        for i, amp in enumerate(self.history):
            height_px = int(amp * (h / 2 - 5))
            cor = COLOR_ACCENT if amp < 0.8 else COLOR_RED
            x = int(i * step)
            self.create_line(x, mid_y - height_px, x, mid_y + height_px, fill=cor, width=2 if step > 2 else 1)

class AnalogVUMeter(tk.Canvas):
    def __init__(self, master, label="VU", **kwargs):
        super().__init__(master, bg=COLOR_BG, highlightthickness=1, highlightbackground=COLOR_TEXT3, width=90, height=120, **kwargs)
        self.label = label
        self.style = 0
        self.bind("<Button-1>", self.toggle_style)
        self.draw(0.0, 0.0)
        
    def toggle_style(self, event=None):
        self.style = (self.style + 1) % 3
        self.draw(0.0, 0.0)
        
    def draw(self, vu_L, vu_R):
        self.delete("all")
        self.create_text(45, 12, text=self.label, fill=COLOR_TEXT2, font=(FONT_FAMILY, 8, "bold"))
        self.create_text(25, 114, text="L", fill=COLOR_TEXT2, font=(FONT_FAMILY, 8, "bold"))
        self.create_text(65, 114, text="R", fill=COLOR_TEXT2, font=(FONT_FAMILY, 8, "bold"))
        
        self._draw_channel(25, vu_L)
        self._draw_channel(65, vu_R)

    def _draw_channel(self, x, amplitude):
        h_bar = 84
        y_bottom = 105
        
        def get_color(ratio):
            if ratio > 0.8: return COLOR_RED
            if ratio > 0.6: return COLOR_YELLOW
            return COLOR_GREEN

        if self.style == 0:
            blocks = 14
            seg_h = 4
            seg_gap = 2
            filled = int(amplitude * blocks * 2.5)
            if filled > blocks: filled = blocks
            for i in range(blocks):
                y_bot = y_bottom - i * (seg_h + seg_gap)
                y_top = y_bot - seg_h
                ratio = (i+1)/blocks
                cor = get_color(ratio) if i < filled else COLOR_SURFACE2
                self.create_rectangle(x-8, y_top, x+8, y_bot, fill=cor, outline="")
        elif self.style == 1:
            self.create_rectangle(x-8, y_bottom-h_bar, x+8, y_bottom, fill=COLOR_SURFACE2, outline="")
            if amplitude > 0:
                y = y_bottom - min(1.0, amplitude * 2.5)*h_bar
                self.create_rectangle(x-8, y, x+8, y_bottom, fill=get_color(amplitude*2.5), outline="")
        elif self.style == 2:
            self.create_line(x, y_bottom, x, y_bottom-h_bar, fill=COLOR_SURFACE2, width=3)
            if amplitude > 0:
                y = y_bottom - min(1.0, amplitude * 2.5)*h_bar
                self.create_line(x, y_bottom, x, y, fill=get_color(amplitude*2.5), width=3)
                self.create_oval(x-4, y-4, x+4, y+4, fill="#FFF", outline="")

class VirtualTurntable(customtkinter.CTkFrame):
    def __init__(self, master, app=None, **kwargs):
        super().__init__(master, fg_color="transparent", **kwargs)
        self.app = app
        self.angle = 0
        self.is_playing = False
        self.label_path = None
        self.tk_label_img = None
        
        self.canvas = customtkinter.CTkCanvas(self, bg=COLOR_BG, highlightthickness=0)
        self.canvas.pack(fill="both", expand=True)
        self.bind("<Configure>", lambda e: self.draw_vinyl())
        self.canvas.bind("<Button-1>", self.open_camera)
        
        ctrl_frame = customtkinter.CTkFrame(self, fg_color="transparent")
        ctrl_frame.pack(fill="x", pady=(5, 0))
        self.btn_add_label = customtkinter.CTkButton(ctrl_frame, text="📸 Alterar Rótulo", width=120, height=24, fg_color=COLOR_SURFACE2, font=FONT_MAIN, command=self.open_camera)
        self.btn_add_label.pack()
        
    def open_camera(self, event=None):
        if not self.app: return
        import cv2, threading
        from PIL import Image
        if hasattr(self, 'cam_window') and self.cam_window.winfo_exists():
            return
            
        self.cam_window = customtkinter.CTkToplevel(self)
        self.cam_window.title("Tirar Foto do Rótulo Central")
        self.cam_window.geometry("500x550")
        self.cam_window.attributes("-topmost", True)
        
        lbl_cam = customtkinter.CTkLabel(self.cam_window, text="Ligando Câmera... Aguarde.", font=FONT_TITLE, text_color=COLOR_TEXT)
        lbl_cam.pack(fill="both", expand=True)
        
        btn_take = customtkinter.CTkButton(self.cam_window, text="📸 CAPTURAR RÓTULO", height=40, font=FONT_BOLD, fg_color=COLOR_ACCENT, text_color="#000", state="disabled")
        btn_take.pack(pady=(10, 5))
        
        cap = [None]
        self.taking_photo = False
        
        def init_camera():
            c = cv2.VideoCapture(0)
            if self.cam_window.winfo_exists():
                cap[0] = c
                lbl_cam.configure(text="")
                btn_take.configure(state="normal")
                update_cam()
            else:
                c.release()
                
        threading.Thread(target=init_camera, daemon=True).start()
        
        def load_from_pc():
            from tkinter import filedialog
            import shutil
            path = filedialog.askopenfilename(title="Escolha a imagem", filetypes=[("Imagens", "*.jpg *.jpeg *.png")])
            if path:
                save_path = os.path.join(self.app.project_dir, "label.jpg")
                try:
                    img = Image.open(path).convert("RGB")
                    w, h = img.size
                    min_dim = min(h, w)
                    sx, sy = (w - min_dim) // 2, (h - min_dim) // 2
                    img = img.crop((sx, sy, sx+min_dim, sy+min_dim))
                    img.save(save_path, quality=90)
                except:
                    shutil.copy(path, save_path)
                
                self.label_path = save_path
                if cap[0]: cap[0].release()
                if self.cam_window.winfo_exists():
                    self.cam_window.destroy()
                self.draw_vinyl()
                
        btn_pc = customtkinter.CTkButton(self.cam_window, text="📁 CARREGAR DO PC", height=40, font=FONT_BOLD, fg_color=COLOR_SURFACE2, text_color=COLOR_TEXT, command=load_from_pc)
        btn_pc.pack(pady=(0, 10))
        
        def update_cam():
            if not hasattr(self, 'cam_window') or not self.cam_window.winfo_exists():
                if cap[0]: cap[0].release()
                return
            if not cap[0]: return
            ret, frame = cap[0].read()
            if ret:
                frame = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
                h, w, _ = frame.shape
                min_dim = min(h, w)
                sx, sy = (w - min_dim) // 2, (h - min_dim) // 2
                frame_sq = frame[sy:sy+min_dim, sx:sx+min_dim]
                img = Image.fromarray(frame_sq).resize((200, 200))
                
                if self.taking_photo:
                    save_path = os.path.join(self.app.project_dir, "label.jpg")
                    img.save(save_path, quality=90)
                    cap[0].release()
                    self.cam_window.destroy()
                    self.label_path = save_path
                    self.draw_vinyl()
                    return
                
                ctk_img = customtkinter.CTkImage(light_image=img, size=(200, 200))
                lbl_cam.configure(image=ctk_img)
            self.after(30, update_cam)
            
        btn_take.configure(command=lambda: setattr(self, 'taking_photo', True))
        
    def set_playing(self, playing: bool):
        self.is_playing = playing
        
    def update_rotation(self):
        if self.is_playing:
            self.angle = (self.angle + 5) % 360
            self.draw_vinyl()
        
    def draw_vinyl(self):
        self.canvas.delete("all")
        w = self.winfo_width()
        h = self.winfo_height()
        if w < 10 or h < 10: return
        
        cx, cy = w // 2, h // 2
        r = min(w, h) // 2 - 10
        
        # outer black
        self.canvas.create_oval(cx-r, cy-r, cx+r, cy+r, fill="#111", outline="#333", width=2)
        # grooves
        for i in range(1, 5):
            gr = r - (r * 0.15 * i)
            self.canvas.create_oval(cx-gr, cy-gr, cx+gr, cy+gr, outline="#222")
            
        label_r = r * 0.35
        import math
        from PIL import Image, ImageTk, ImageDraw
        
        if self.label_path and os.path.exists(self.label_path):
            img = Image.open(self.label_path).convert("RGBA")
            size = int(label_r * 2)
            img = img.resize((size, size))
            mask = Image.new('L', (size, size), 0)
            draw = ImageDraw.Draw(mask)
            draw.ellipse((0, 0, size, size), fill=255)
            img.putalpha(mask)
            
            img = img.rotate(-self.angle)
            self.tk_label_img = ImageTk.PhotoImage(img)
            self.canvas.create_image(cx, cy, image=self.tk_label_img)
        else:
            self.canvas.create_oval(cx-label_r, cy-label_r, cx+label_r, cy+label_r, fill=COLOR_ACCENT, outline="")
            rad = math.radians(self.angle)
            lx = cx + (label_r * 0.5) * math.cos(rad)
            ly = cy + (label_r * 0.5) * math.sin(rad)
            self.canvas.create_oval(lx-4, ly-4, lx+4, ly+4, fill="#000", outline="")
            self.canvas.create_text(cx, cy-15, text="📸 RÓTULO", fill="#000", font=(FONT_FAMILY, 10, "bold"))
            
        self.canvas.create_oval(cx-5, cy-5, cx+5, cy+5, fill=COLOR_BG, outline="")

class CoverDisplay(customtkinter.CTkFrame):
    def __init__(self, master, app=None, title="📸 ADICIONAR CAPA", **kwargs):
        super().__init__(master, fg_color="transparent", **kwargs)
        self.app = app
        self.cover_path = None
        self.title_text = title
        
        self.lbl_img = customtkinter.CTkLabel(self, text=title, fg_color=COLOR_BG, font=FONT_TITLE, text_color=COLOR_SURFACE2)
        self.lbl_img.pack(fill="both", expand=True)
        self.lbl_img.bind("<Button-1>", self.open_camera)
        
        ctrl_frame = customtkinter.CTkFrame(self, fg_color="transparent")
        ctrl_frame.pack(fill="x", pady=(5, 0))
        
        self.btn_add_cover = customtkinter.CTkButton(ctrl_frame, text="📸 Alterar Imagem", height=24, fg_color=COLOR_SURFACE2, font=FONT_MAIN, command=self.open_camera)
        self.btn_add_cover.pack(fill="x", expand=True)

    def open_camera(self, event=None):
        import cv2, threading
        from PIL import Image, ImageTk
        if hasattr(self, 'cam_window') and self.cam_window.winfo_exists():
            return
            
        self.cam_window = customtkinter.CTkToplevel(self)
        self.cam_window.title("Tirar Foto da Capa")
        self.cam_window.geometry("500x550")
        self.cam_window.attributes("-topmost", True)
        
        lbl_cam = customtkinter.CTkLabel(self.cam_window, text="Ligando Câmera... Aguarde.", font=FONT_TITLE, text_color=COLOR_TEXT)
        lbl_cam.pack(fill="both", expand=True)
        
        btn_take = customtkinter.CTkButton(self.cam_window, text="📸 CAPTURAR CAPA", height=40, font=FONT_BOLD, fg_color=COLOR_ACCENT, text_color="#000", state="disabled")
        btn_take.pack(pady=(10, 5))
        
        cap = [None]
        self.taking_photo = False
        
        def init_camera():
            c = cv2.VideoCapture(0)
            if self.cam_window.winfo_exists():
                cap[0] = c
                lbl_cam.configure(text="")
                btn_take.configure(state="normal")
                update_cam()
            else:
                c.release()
                
        threading.Thread(target=init_camera, daemon=True).start()
        
        def load_from_pc():
            from tkinter import filedialog
            import shutil
            path = filedialog.askopenfilename(title="Escolha a imagem da capa", filetypes=[("Imagens", "*.jpg *.jpeg *.png")])
            if path:
                save_path = os.path.join(self.app.project_dir, "cover.jpg")
                try:
                    img = Image.open(path).convert("RGB")
                    w, h = img.size
                    min_dim = min(h, w)
                    sx, sy = (w - min_dim) // 2, (h - min_dim) // 2
                    img = img.crop((sx, sy, sx+min_dim, sy+min_dim))
                    img.save(save_path, quality=90)
                except:
                    shutil.copy(path, save_path)
                
                if cap[0]: cap[0].release()
                if self.cam_window.winfo_exists():
                    self.cam_window.destroy()
                self.load_cover(save_path)
                
        btn_pc = customtkinter.CTkButton(self.cam_window, text="📁 CARREGAR DO PC", height=40, font=FONT_BOLD, fg_color=COLOR_SURFACE2, text_color=COLOR_TEXT, command=load_from_pc)
        btn_pc.pack(pady=(0, 10))
        
        def update_cam():
            if not hasattr(self, 'cam_window') or not self.cam_window.winfo_exists():
                if cap[0]: cap[0].release()
                return
            if not cap[0]: return
            ret, frame = cap[0].read()
            if ret:
                frame = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
                h, w, _ = frame.shape
                min_dim = min(h, w)
                sx, sy = (w - min_dim) // 2, (h - min_dim) // 2
                frame_sq = frame[sy:sy+min_dim, sx:sx+min_dim]
                img = Image.fromarray(frame_sq).resize((400, 400))
                
                if self.taking_photo:
                    save_path = os.path.join(self.app.project_dir, "cover.jpg")
                    img.save(save_path, quality=90)
                    cap[0].release()
                    self.cam_window.destroy()
                    self.load_cover(save_path)
                    return
                
                ctk_img = customtkinter.CTkImage(light_image=img, size=(400, 400))
                lbl_cam.configure(image=ctk_img)
            self.after(30, update_cam)
            
        btn_take.configure(command=lambda: setattr(self, 'taking_photo', True))
        
    def load_cover(self, path):
        self.cover_path = path
        if not path or not os.path.exists(path):
            self.lbl_img.configure(image="", text=self.title_text)
            return
        from PIL import Image, ImageTk
        img = Image.open(path).convert("RGB")
        img = img.resize((400, 400))
        self.tk_img = customtkinter.CTkImage(light_image=img, size=(400, 400))
        self.lbl_img.configure(image=self.tk_img, text="")
        
    def flip_cover(self):
        pass


CARTRIDGES = [
    # Leson / Nacionais (Brasil)
    "Leson AG-180 Diamante", "Leson Axxis", "Leson Axxis I", "Leson Axxis II", "Leson AG-80", "Leson AG-90", "Leson PTC", "Leson LM",
    # Audio-Technica
    "Audio-Technica AT95E", "Audio-Technica AT-VM95C", "Audio-Technica AT-VM95E", "Audio-Technica AT-VM95EN", "Audio-Technica AT-VM95ML", "Audio-Technica AT-VM95SH",
    "Audio-Technica AT3600L", "Audio-Technica AT81CP", "Audio-Technica AT85EP", "Audio-Technica AT-VM520EB", "Audio-Technica AT-VM530EN",
    "Audio-Technica AT-VM540ML", "Audio-Technica AT-VM740ML", "Audio-Technica AT-VM750SH", "Audio-Technica AT-VM760SLC", "Audio-Technica AT-OC9XEB", 
    "Audio-Technica AT-OC9XEN", "Audio-Technica AT-OC9XML", "Audio-Technica AT-OC9XSH", "Audio-Technica AT-OC9XSL", "Audio-Technica AT-ART9XI",
    # Ortofon
    "Ortofon 2M Red", "Ortofon 2M Blue", "Ortofon 2M Bronze", "Ortofon 2M Black", "Ortofon 2M Black LVB 250", "Ortofon 2M Mono", "Ortofon 2M 78",
    "Ortofon OM5E", "Ortofon OM10", "Ortofon OM20", "Ortofon OM30", "Ortofon OM40", "Ortofon Super OM5E", 
    "Ortofon Concorde Mix", "Ortofon Concorde DJ", "Ortofon Concorde Club", "Ortofon Concorde Scratch", "Ortofon Concorde Digital", 
    "Ortofon Quintet Red", "Ortofon Quintet Blue", "Ortofon Quintet Bronze", "Ortofon Quintet Black S", 
    "Ortofon Cadenza Red", "Ortofon Cadenza Blue", "Ortofon Cadenza Bronze", "Ortofon Cadenza Black",
    # Shure
    "Shure M44-7", "Shure M44G", "Shure M97xE", "Shure V15 Type III", "Shure V15 Type IV", "Shure V15 Type V", "Shure SC35C", "Shure M92E", "Shure Whitelabel", "Shure M75ED",
    # Nagaoka
    "Nagaoka MP-110", "Nagaoka MP-150", "Nagaoka MP-200", "Nagaoka MP-300", "Nagaoka MP-500", "Nagaoka JT-80BK", "Nagaoka JT-80LB",
    # Goldring
    "Goldring E1", "Goldring E2", "Goldring E3", "Goldring E4", "Goldring 1006", "Goldring 1012GX", "Goldring 1022GX", "Goldring 1042", "Goldring Eroica LX", "Goldring Eroica H", "Goldring Elite", "Goldring Ethos",
    # Rega
    "Rega Carbon", "Rega Nd3", "Rega Nd5", "Rega Nd7", "Rega Bias 2", "Rega Elys 2", "Rega Exact", "Rega Ania", "Rega Ania Pro", "Rega Apheta 3", "Rega Aphelion 2",
    # Denon
    "Denon DL-103", "Denon DL-103R", "Denon DL-110", "Denon DL-301 II", "Denon DL-160", "Denon DL-A110",
    # Sumiko
    "Sumiko Oyster", "Sumiko Black Pearl", "Sumiko Pearl", "Sumiko Rainier", "Sumiko Olympia", "Sumiko Moonstone", "Sumiko Wellfleet", "Sumiko Amethyst",
    "Sumiko Blue Point No. 3", "Sumiko Songbird", "Sumiko Starling",
    # Grado
    "Grado Prestige Black", "Grado Prestige Green", "Grado Prestige Blue", "Grado Prestige Red", "Grado Prestige Silver", "Grado Prestige Gold",
    "Grado Opus3", "Grado Platinum3", "Grado Sonata3", "Grado Master3", "Grado Reference3",
    # Clearaudio
    "Clearaudio Concept V2", "Clearaudio Performer V2", "Clearaudio Artist V2", "Clearaudio Virtuoso V2", "Clearaudio Maestro V2",
    "Clearaudio Concept MC", "Clearaudio Essence MC", "Clearaudio Talismann V2 Gold", "Clearaudio Concerto V2", "Clearaudio Stradivari V2",
    # Stanton
    "Stanton 500", "Stanton 500 AL", "Stanton 680", "Stanton 681EEE", "Stanton 881S", "Stanton Trackmaster", "Stanton Groovemaster",
    # Pickering
    "Pickering V-15", "Pickering XV-15", "Pickering XSV/3000",
    # Dynavector
    "Dynavector 10X5 MkII", "Dynavector 20X2", "Dynavector Karat 17DX", "Dynavector XX-2 MkII", "Dynavector Te Kaitora Rua", "Dynavector DRT XV-1s",
    # Soundsmith
    "Soundsmith Otello", "Soundsmith Carmen", "Soundsmith Zephyr", "Soundsmith Aida", "Soundsmith Sussurro", "Soundsmith Paua",
    # Lyra
    "Lyra Delos", "Lyra Kleos", "Lyra Etna", "Lyra Atlas",
    # Benz Micro
    "Benz Micro MC Gold", "Benz Micro MC Silver", "Benz Micro ACE", "Benz Micro Glider", "Benz Micro Wood", "Benz Micro Zebra", "Benz Micro Ruby", "Benz Micro Gullwing",
    # Hana
    "Hana E", "Hana S", "Hana M", "Hana Umami Red", "Hana Umami Blue",
    # Koetsu
    "Koetsu Black", "Koetsu Rosewood", "Koetsu Urushi", "Koetsu Onyx Platinum",
    # Pioneer / DJ / Numark
    "Pioneer PC-HS01", "Pioneer PN-X05", "Numark CC-1", "Numark CS-1", "Numark Groovetool",
    # ION / Crosley / Genéricas
    "ION (Agulha Cerâmica Padrão Pz51)", "ION (Agulha Safira/Rubi CZ-800)", "Chuo Denshi CZ-800", "Crosley NP1 / NP6"
]

class MetadataCard(customtkinter.CTkFrame):
    def __init__(self, master, on_album_found=None, **kwargs):
        super().__init__(master, fg_color=COLOR_SURFACE, corner_radius=0, border_width=1, border_color=COLOR_SURFACE2, **kwargs)
        self.on_album_found = on_album_found
        self.is_collapsed = False
        
        top_frame = customtkinter.CTkFrame(self, fg_color="transparent")
        top_frame.pack(fill="x", pady=(10, 5), padx=15)
        
        lbl_title = customtkinter.CTkLabel(top_frame, text="[ ADICIONAR INFORMAÇÕES DO DISCO ]", font=FONT_BOLD, text_color=COLOR_TEXT2)
        lbl_title.pack(side="left")
        
        btn_toggle = customtkinter.CTkButton(top_frame, text="[-]", width=30, height=24, fg_color="transparent", font=FONT_BOLD, command=self.toggle)
        btn_toggle.pack(side="right", padx=(10,0))
        
        btn_search = customtkinter.CTkButton(top_frame, text="🔍 Buscar Álbum", width=120, height=24, fg_color=COLOR_SURFACE2, font=FONT_MAIN, command=self.search_album)
        btn_search.pack(side="right")
        
        self.content_frame = customtkinter.CTkFrame(self, fg_color="transparent")
        self.content_frame.pack(fill="both", expand=True)
        
        self.btn_toggle = btn_toggle
        
        self.entries = {}
        fields = [("Artista / Banda", "artist"), ("Álbum", "album"), ("Ano", "year"), ("Agulha/Cápsula", "cartridge"), ("Vinil (ex: 180g)", "vinyl_spec")]
        for label_text, key in fields:
            f = customtkinter.CTkFrame(self.content_frame, fg_color="transparent")
            f.pack(fill="x", padx=15, pady=1)
            customtkinter.CTkLabel(f, text=label_text, font=FONT_MAIN, text_color=COLOR_TEXT, width=150, anchor="e").pack(side="left", padx=(0, 10))
            
            if key == "cartridge":
                ent = customtkinter.CTkComboBox(f, values=CARTRIDGES, fg_color=COLOR_BG, border_color=COLOR_SURFACE2, text_color=COLOR_TEXT, font=FONT_MAIN, command=lambda v: None)
                ent.set("")
                ent.bind("<KeyRelease>", self._filter_cartridges)
            else:
                ent = customtkinter.CTkEntry(f, fg_color=COLOR_BG, border_color=COLOR_SURFACE2, text_color=COLOR_TEXT, font=FONT_MAIN)
            ent.pack(side="left", fill="x", expand=True)
            self.entries[key] = ent

    def toggle(self):
        if self.is_collapsed:
            self.content_frame.pack(fill="both", expand=True)
            self.btn_toggle.configure(text="[-]")
            self.is_collapsed = False
        else:
            self.content_frame.pack_forget()
            self.btn_toggle.configure(text="[+]")
            self.is_collapsed = True

    def _filter_cartridges(self, event):
        if event.keysym in ("Down", "Up", "Left", "Right", "Return", "Escape", "Tab"):
            return
            
        typed = self.entries["cartridge"].get().lower()
        hits = [c for c in CARTRIDGES if typed in c.lower()]
        
        cb = self.entries["cartridge"]
        # Fecha menu antigo para recriar com novos valores
        if hasattr(cb, "_dropdown_menu") and cb._dropdown_menu.winfo_ismapped():
            cb._dropdown_menu._withdraw()
            
        cb.configure(values=hits if hits else CARTRIDGES)
        
        if typed and hits:
            if hasattr(cb, "_open_dropdown_menu"):
                cb._open_dropdown_menu()
                # Devolve o foco para o campo de texto para continuar digitando
                if hasattr(cb, "_entry"):
                    cb._entry.focus_set()
        
    def search_album(self):
        import urllib.request
        import json
        
        artist = self.entries["artist"].get().strip()
        album = self.entries["album"].get().strip()
        if not artist or not album:
            messagebox.showwarning("Busca", "Digite pelo menos o nome do Artista e do Álbum para buscar!")
            return
            
        try:
            query = urllib.parse.quote(f'artist:"{artist}" AND release:"{album}"')
            url = f"https://musicbrainz.org/ws/2/release/?query={query}&fmt=json"
            req = urllib.request.Request(url, headers={'User-Agent': 'OpenVynilRipper/1.0 ( conrider3000@github )'})
            with urllib.request.urlopen(req) as response:
                data = json.loads(response.read().decode())
                
            if data.get("releases"):
                res = data["releases"][0]
                
                # Preencher Artista (Pegando o primeiro artist-credit)
                if res.get("artist-credit"):
                    self.entries["artist"].delete(0, 'end')
                    self.entries["artist"].insert(0, res["artist-credit"][0].get("name", artist))
                    
                # Preencher Álbum
                self.entries["album"].delete(0, 'end')
                self.entries["album"].insert(0, res.get("title", album))
                
                # Preencher Ano
                year = res.get("date", "")[:4]
                if year:
                    self.entries["year"].delete(0, 'end')
                    self.entries["year"].insert(0, year)
                    
                messagebox.showinfo("Sucesso", f"Álbum encontrado na base de dados global (MusicBrainz)!\n\n{res.get('title')} ({year})")
                
                release_id = res.get("id")
                if release_id and self.on_album_found:
                    self.on_album_found(release_id)
                    
            else:
                messagebox.showinfo("Não encontrado", "Não encontramos esse álbum exato na base do MusicBrainz.")
        except Exception as e:
            messagebox.showerror("Erro", f"Erro de conexão: {e}")

    def get_metadata(self):
        return {key: ent.get().strip() for key, ent in self.entries.items()}

class CollapsibleFrame(customtkinter.CTkFrame):
    def __init__(self, master, title, **kwargs):
        super().__init__(master, fg_color=COLOR_SURFACE, corner_radius=8, border_width=2, border_color="#333", **kwargs)
        self.is_collapsed = False
        
        self.header = customtkinter.CTkFrame(self, fg_color="transparent")
        self.header.pack(fill="x", padx=15, pady=10)
        
        self.lbl_title = customtkinter.CTkLabel(self.header, text=title, font=FONT_BOLD, text_color=COLOR_TEXT)
        self.lbl_title.pack(side="left")
        
        self.btn_toggle = customtkinter.CTkButton(self.header, text="▼", width=30, height=24, fg_color=COLOR_SURFACE2, text_color=COLOR_TEXT, font=FONT_BOLD, corner_radius=4, command=self.toggle)
        self.btn_toggle.pack(side="right")
        
        self.content_frame = customtkinter.CTkFrame(self, fg_color="transparent")
        self.content_frame.pack(fill="both", expand=True)
        
    def toggle(self):
        if self.is_collapsed:
            self.content_frame.pack(fill="both", expand=True)
            self.btn_toggle.configure(text="▼")
            self.is_collapsed = False
        else:
            self.content_frame.pack_forget()
            self.btn_toggle.configure(text="▲")
            self.is_collapsed = True

class FileBrowser(customtkinter.CTkFrame):
    def __init__(self, master, app, **kwargs):
        super().__init__(master, fg_color="transparent", **kwargs)
        self.app = app
        
        top = customtkinter.CTkFrame(self, fg_color="transparent")
        top.pack(fill="x", pady=(0, 5))
        
        self.lbl_path = customtkinter.CTkLabel(top, text="...", font=FONT_MAIN, text_color=COLOR_TEXT)
        self.lbl_path.pack(side="left", fill="x", expand=True, padx=(0, 5))
        
        def open_explorer():
            import os
            os.startfile(self.app.project_dir)
            
        customtkinter.CTkButton(top, text="📁↗", width=40, height=24, fg_color=COLOR_SURFACE2, font=FONT_MAIN, command=open_explorer).pack(side="right", padx=(5,0))
        customtkinter.CTkButton(top, text="Alterar", width=60, height=24, fg_color=COLOR_SURFACE2, font=FONT_MAIN, command=self.app._choose_folder).pack(side="right")
        
        self.scroll = customtkinter.CTkScrollableFrame(self, fg_color=COLOR_BG, border_width=1, border_color=COLOR_SURFACE2)
        self.scroll.pack(fill="both", expand=True, pady=5)
        
        customtkinter.CTkButton(self, text="+ Nova Pasta", height=24, fg_color=COLOR_SURFACE2, font=FONT_MAIN, command=self.new_folder).pack(fill="x")
        
    def update_browser(self):
        self.lbl_path.configure(text=os.path.basename(self.app.project_dir) or self.app.project_dir)
        for w in self.scroll.winfo_children(): w.destroy()
        
        parent = os.path.dirname(self.app.project_dir)
        try:
            for d in os.listdir(parent):
                full = os.path.join(parent, d)
                if os.path.isdir(full):
                    btn = customtkinter.CTkButton(self.scroll, text=f"📁 {d}", fg_color="transparent", text_color=COLOR_TEXT, anchor="w", command=lambda p=full: self.app.set_project_dir(p))
                    btn.pack(fill="x", pady=1)
        except: pass

    def new_folder(self):
        import tkinter.simpledialog
        name = tkinter.simpledialog.askstring("Nova Pasta", "Nome do novo disco:")
        if name:
            new_path = os.path.join(os.path.dirname(self.app.project_dir), name)
            os.makedirs(new_path, exist_ok=True)
            self.app.set_project_dir(new_path)

class App(customtkinter.CTk):
    def __init__(self):
        super().__init__()
        self.engine = AudioEngine()
        self.engine.on_time_update = self.on_time_update
        self.project_dir = os.path.join(os.path.expanduser("~"), "Music", "OpenVynilRipper", "MeuDisco")
        os.makedirs(self.project_dir, exist_ok=True)
        
        self.title("OPEN VYNIL RIPPER - ANALOG EDITION")
        self.geometry("950x950")
        self.resizable(True, True) # Permite Tela Cheia
        self.configure(fg_color=COLOR_BG)
        self.protocol("WM_DELETE_WINDOW", self.on_closing)
        
        self._build_ui()
        self._init_devices()
        self._ui_update_loop()
        

    def _build_ui(self):
        header = customtkinter.CTkFrame(self, fg_color=COLOR_SURFACE, height=50, corner_radius=0)
        header.pack(fill="x", pady=(0, 10))
        customtkinter.CTkLabel(header, text="O P E N   V Y N I L   R I P P E R", font=FONT_TITLE, text_color=COLOR_ACCENT).pack(pady=10)
        
        # We will pack the Visualizer Frame in the middle, and the Bottom Frame at the bottom
        vis_frame = customtkinter.CTkFrame(self, fg_color=COLOR_SURFACE, corner_radius=0, border_width=1, border_color=COLOR_SURFACE2)
        vis_frame.pack(fill="x", padx=10, pady=(5, 0), side="bottom")
        
        bottom_row = customtkinter.CTkFrame(self, fg_color="transparent")
        bottom_row.pack(fill="x", padx=10, pady=5, side="bottom")
        bottom_row.columnconfigure(0, weight=1, uniform="c")
        bottom_row.columnconfigure(1, weight=1, uniform="c")
        
        main_container = customtkinter.CTkFrame(self, fg_color="transparent")
        main_container.pack(fill="both", expand=True, padx=10, pady=5)
        
        top_row = customtkinter.CTkFrame(main_container, fg_color="transparent")
        top_row.pack(fill="x")
        top_row.columnconfigure(0, weight=1, uniform="a")
        top_row.columnconfigure(1, weight=1, uniform="a")
        
        # Audio Connections (Collapsible)
        dev_frame = CollapsibleFrame(top_row, title="[ CONEXÕES DE ÁUDIO ]")
        dev_frame.grid(row=0, column=0, sticky="nsew", padx=(0, 5))
        dev_content = dev_frame.content_frame
        
        self.opt_in = customtkinter.CTkOptionMenu(dev_content, values=["Nenhum"], font=FONT_MAIN, fg_color=COLOR_BG, button_color=COLOR_SURFACE2, text_color=COLOR_TEXT, command=self._on_device_change)
        self.opt_in.pack(fill="x", padx=15, pady=5)
        self.opt_out = customtkinter.CTkOptionMenu(dev_content, values=["Nenhum"], font=FONT_MAIN, fg_color=COLOR_BG, button_color=COLOR_SURFACE2, text_color=COLOR_TEXT, command=self._on_device_change)
        self.opt_out.pack(fill="x", padx=15, pady=5)
        
        self.meta_card = MetadataCard(top_row, on_album_found=self._fetch_cover_art)
        self.meta_card.grid(row=0, column=1, sticky="nsew", padx=(5, 0))
        
        # 3 Columns Layout (Mid Row)
        mid_row = customtkinter.CTkFrame(main_container, fg_color="transparent")
        mid_row.pack(fill="both", expand=True, pady=(10,0))
        mid_row.columnconfigure(0, weight=1, uniform="b")
        mid_row.columnconfigure(1, weight=1, uniform="b")
        mid_row.columnconfigure(2, weight=1, uniform="b")
        mid_row.rowconfigure(0, weight=1)
        
        self.turntable = VirtualTurntable(mid_row, app=self)
        self.turntable.grid(row=0, column=0, sticky="nsew", padx=(0, 5))
        
        # Pass the Pink Floyd defaults
        pf_front = os.path.join(os.path.expanduser("~"), "Music", "OpenVynilRipper", "assets", "default_front.jpg")
        pf_back = os.path.join(os.path.expanduser("~"), "Music", "OpenVynilRipper", "assets", "default_back.jpg")
        
        self.cover_front = CoverDisplay(mid_row, app=self, title="Capa Frontal")
        self.cover_front.grid(row=0, column=1, sticky="nsew", padx=(5, 5))
        self.cover_front.load_cover(pf_front)
        
        self.cover_back = CoverDisplay(mid_row, app=self, title="Contracapa")
        self.cover_back.grid(row=0, column=2, sticky="nsew", padx=(5, 0))
        self.cover_back.load_cover(pf_back)
        
        # Visualizer with Knobs
        self.knob_in = RotaryKnob(vis_frame, label="INPUT GAIN", command=self._on_input_gain, init_val=1.0, max_val=3.0)
        self.knob_in.pack(side="left", padx=(15, 5), pady=15)
        self.vu_in = AnalogVUMeter(vis_frame, label="INPUT VOL.")
        self.vu_in.pack(side="left", padx=(5, 15), pady=15)
        
        self.waveform = RetroWaveform(vis_frame)
        self.waveform.pack(side="left", expand=True, fill="both", pady=15)
        
        self.vu_out = AnalogVUMeter(vis_frame, label="MONITOR VOL.")
        self.vu_out.pack(side="right", padx=(15, 5), pady=15)
        self.knob_out = RotaryKnob(vis_frame, label="MONITOR GAIN", command=self._on_monitor_gain, init_val=1.0, max_val=2.0)
        self.knob_out.pack(side="right", padx=(5, 15), pady=15)
        
        # Bottom Recorder vs Browser
        rec_col = CollapsibleFrame(bottom_row, title="[ G R A V A D O R ]")
        rec_col.grid(row=0, column=0, sticky="nsew", padx=(0, 5))
        trans_frame = rec_col.content_frame
        
        browser_col = CollapsibleFrame(bottom_row, title="[ M E U   D I S C O ]")
        browser_col.grid(row=0, column=1, sticky="nsew", padx=(5, 0))
        self.browser = FileBrowser(browser_col.content_frame, app=self)
        self.browser.pack(fill="both", expand=True, padx=15, pady=10)
        self.browser.update_browser()
        
        # Recorder UI
        t_top = customtkinter.CTkFrame(trans_frame, fg_color="transparent")
        t_top.pack(fill="x", padx=15, pady=5)
        
        self.chk_magic = customtkinter.CTkSwitch(t_top, text="✨ Magic Record (Início Automático)", fg_color="#8a2be2", text_color=COLOR_TEXT, font=FONT_BOLD)
        self.chk_magic.pack(pady=(0, 10))
        
        r_btns = customtkinter.CTkFrame(t_top, fg_color="transparent")
        r_btns.pack(fill="x")
        self.lbl_timer = customtkinter.CTkLabel(r_btns, text="00:00.00", font=(FONT_FAMILY, 28, "bold"), text_color=COLOR_RED)
        self.lbl_timer.pack(side="left", padx=(0, 15))
        self.btn_rec_a = customtkinter.CTkButton(r_btns, text="● GRAVAR LADO A", fg_color=COLOR_RED, text_color="#FFF", font=FONT_BOLD, height=35, corner_radius=6, command=lambda: self.on_rec_side("LadoA"))
        self.btn_rec_a.pack(side="left", padx=(0, 5), expand=True, fill="x")
        self.btn_rec_b = customtkinter.CTkButton(r_btns, text="● GRAVAR LADO B", fg_color=COLOR_RED, text_color="#FFF", font=FONT_BOLD, height=35, corner_radius=6, command=lambda: self.on_rec_side("LadoB"))
        self.btn_rec_b.pack(side="left", padx=5, expand=True, fill="x")
        self.btn_stop = customtkinter.CTkButton(r_btns, text="■ STOP", fg_color=COLOR_SURFACE2, text_color=COLOR_TEXT, font=FONT_BOLD, height=35, corner_radius=6, state="disabled", command=self.on_stop_click)
        self.btn_stop.pack(side="left", padx=5)
        
        t_mid = customtkinter.CTkFrame(trans_frame, fg_color="transparent")
        t_mid.pack(fill="x", padx=15, pady=10)
        
        sw_f = customtkinter.CTkFrame(t_mid, fg_color="transparent")
        sw_f.pack(side="left", fill="y", padx=(0, 20))
        self.chk_normalize = customtkinter.CTkSwitch(sw_f, text="Normalizar Volume", fg_color=COLOR_ACCENT, text_color=COLOR_TEXT, font=FONT_MAIN)
        self.chk_normalize.pack(anchor="w", pady=4)
        self.chk_denoise = customtkinter.CTkSwitch(sw_f, text="Filtro Anti-Chiado", fg_color=COLOR_ACCENT, text_color=COLOR_TEXT, font=FONT_MAIN)
        self.chk_denoise.pack(anchor="w", pady=4)
        self.chk_clipping = customtkinter.CTkSwitch(sw_f, text="Remover Clippings", fg_color=COLOR_ACCENT, text_color=COLOR_TEXT, font=FONT_MAIN)
        self.chk_clipping.pack(anchor="w", pady=4)
        
        f_opts = customtkinter.CTkFrame(t_mid, fg_color="transparent")
        f_opts.pack(side="left", fill="y", expand=True)
        
        opt_grid1 = customtkinter.CTkFrame(f_opts, fg_color="transparent")
        opt_grid1.pack(fill="x", pady=2)
        customtkinter.CTkLabel(opt_grid1, text="Salvar original em:", font=FONT_BOLD, text_color=COLOR_TEXT2).pack(side="left")
        self.opt_format = customtkinter.CTkOptionMenu(opt_grid1, values=["WAV", "FLAC", "MP3", "OGG"], fg_color=COLOR_BG, button_color=COLOR_SURFACE2, font=FONT_MAIN, width=80)
        self.opt_format.pack(side="right")
        
        opt_grid2 = customtkinter.CTkFrame(f_opts, fg_color="transparent")
        opt_grid2.pack(fill="x", pady=2)
        customtkinter.CTkLabel(opt_grid2, text="Formato exportação:", font=FONT_BOLD, text_color=COLOR_TEXT2).pack(side="left")
        self.opt_export = customtkinter.CTkOptionMenu(opt_grid2, values=["MP3", "FLAC", "WAV", "OGG"], fg_color=COLOR_BG, button_color=COLOR_SURFACE2, font=FONT_MAIN, width=80)
        self.opt_export.pack(side="right")
        
        self.btn_split = customtkinter.CTkButton(trans_frame, text="✂ SEPARAR FAIXAS AUTOMÁTICO E EXPORTAR", fg_color=COLOR_ACCENT, text_color="#000", font=FONT_BOLD, height=40, corner_radius=8, command=self.on_auto_split)
        self.btn_split.pack(fill="x", padx=15, pady=(5, 10))
        
        self.update_buttons_state()

    def _on_input_gain(self, val): self.engine.input_gain = float(val)
    def _on_monitor_gain(self, val): self.engine.monitor_volume = float(val)
    
    def update_buttons_state(self):
        import os
        if os.path.exists(os.path.join(self.project_dir, "LadoA.wav")):
            self.btn_rec_a.configure(text="● REGRAVAR LADO A", fg_color=COLOR_GREEN)
        else:
            self.btn_rec_a.configure(text="● GRAVAR LADO A", fg_color=COLOR_RED)
            
        if os.path.exists(os.path.join(self.project_dir, "LadoB.wav")):
            self.btn_rec_b.configure(text="● REGRAVAR LADO B", fg_color=COLOR_GREEN)
        else:
            self.btn_rec_b.configure(text="● GRAVAR LADO B", fg_color=COLOR_RED)

    def _init_devices(self):
        devs = self.engine.get_devices()
        self.in_map = {d["name"]: d["index"] for d in devs["inputs"]}
        self.out_map = {d["name"]: d["index"] for d in devs["outputs"]}
        if self.in_map:
            self.opt_in.configure(values=list(self.in_map.keys()))
            self.opt_in.set(next((n for n in self.in_map.keys() if "ION" in n.upper() or "USB" in n.upper()), list(self.in_map.keys())[0]))
        if self.out_map:
            self.opt_out.configure(values=list(self.out_map.keys()))
            self.opt_out.set(next((n for n in self.out_map.keys() if "SYNAPTICS" in n.upper() or "REALTEK" in n.upper()), list(self.out_map.keys())[0]))
        self._start_stream()
        

    def set_project_dir(self, new_dir):
        self.project_dir = new_dir
        self.browser.update_browser()

    def _choose_folder(self):
        folder = filedialog.askdirectory(initialdir=self.project_dir, title="Escolha a pasta do disco")
        if folder:
            self.project_dir = folder
            self.lbl_folder.configure(text=self.project_dir)

    def _on_volume_change(self, val): self.engine.monitor_volume = float(val)
    def _on_device_change(self, val): self.engine.stop_stream(); self._start_stream()
    def _start_stream(self):
        in_name, out_name = self.opt_in.get(), self.opt_out.get()
        if in_name in self.in_map and out_name in self.out_map:
            self.engine.input_device = self.in_map[in_name]
            self.engine.output_device = self.out_map[out_name]
            self.engine.start_stream()


    def on_rec_side(self, side_name):
        meta = self.meta_card.get_metadata()
        if not meta["artist"] or not meta["album"]:
            import tkinter.messagebox as messagebox
            if not messagebox.askyesno("Metadados Vazios", "Você não preencheu Artista e Álbum. Deseja gravar mesmo assim?"):
                return

        wav_path = os.path.join(self.project_dir, f"{side_name}.wav")
        if os.path.exists(wav_path):
            if not messagebox.askyesno("Sobrescrever", f"O arquivo {side_name}.wav já existe.\nDeseja sobrescrever e gravar este lado novamente?"):
                return
                
        if self.chk_magic.get():
            self.engine.magic_wait = True
            self.engine.magic_path = wav_path
            self.engine.on_magic_trigger = lambda: self.after(0, lambda: [self.lbl_status.configure(text=f"GRAVANDO O {side_name.upper()}...", text_color=COLOR_RED), self.turntable.set_playing(True)])
            self.lbl_status.configure(text=f"AGUARDANDO O ÁUDIO COMEÇAR (MAGIC RECORD)...", text_color="#8a2be2")
        else:
            self.engine.start_recording(wav_path)
            self.turntable.set_playing(True)
            self.lbl_status.configure(text=f"GRAVANDO O {side_name.upper()}...", text_color=COLOR_RED)
            
        self.btn_rec_a.configure(state="disabled")
        self.btn_rec_b.configure(state="disabled")
        self.btn_split.configure(state="disabled")
        self.btn_stop.configure(state="normal", fg_color=COLOR_ACCENT)
        self.lbl_status.configure(text=f"GRAVANDO {side_name.upper()}...", text_color=COLOR_RED)
        
    def on_stop_click(self):
        self.engine.stop_recording()
        self.engine.magic_wait = False
        self.turntable.set_playing(False)
        self.btn_rec_a.configure(state="normal")
        self.btn_rec_b.configure(state="normal")
        self.btn_split.configure(state="normal")
        self.btn_stop.configure(state="disabled", fg_color=COLOR_SURFACE2)
        self.lbl_status.configure(text="GRAVAÇÃO FINALIZADA. PRONTO.", text_color=COLOR_GREEN)
        
    def _fetch_cover_art(self, release_id):
        def task():
            import urllib.request, json, os, threading
            try:
                self.after(0, lambda: self.lbl_status.configure(text="BAIXANDO CAPA DO ÁLBUM...", text_color=COLOR_YELLOW))
                url = f"https://coverartarchive.org/release/{release_id}"
                req = urllib.request.Request(url, headers={'User-Agent': 'OpenVynilRipper/1.0'})
                with urllib.request.urlopen(req) as response:
                    data = json.loads(response.read().decode())
                
                front_path, back_path = None, None
                for img in data.get("images", []):
                    if img.get("front") and not front_path:
                        front_path = os.path.join(self.project_dir, "cover.jpg")
                        urllib.request.urlretrieve(img["thumbnails"].get("500", img["image"]), front_path)
                    elif img.get("back") and not back_path:
                        back_path = os.path.join(self.project_dir, "back.jpg")
                        urllib.request.urlretrieve(img["thumbnails"].get("500", img["image"]), back_path)
                        
                self.after(0, lambda: self.cover_front.load_cover(front_path, back_path))
                self.after(0, lambda: self.lbl_status.configure(text="SISTEMA PRONTO", text_color=COLOR_GREEN))
            except Exception as e:
                print("Cover fetch error:", e)
                self.after(0, lambda: self.lbl_status.configure(text="CAPA INDISPONÍVEL", text_color=COLOR_TEXT))
        import threading
        threading.Thread(target=task, daemon=True).start()

    def on_auto_split(self):
        lado_a = os.path.join(self.project_dir, "LadoA.wav")
        lado_b = os.path.join(self.project_dir, "LadoB.wav")
        
        files_to_process = []
        if os.path.exists(lado_a): files_to_process.append(("Lado A", lado_a))
        if os.path.exists(lado_b): files_to_process.append(("Lado B", lado_b))
        
        if not files_to_process:
            messagebox.showwarning("Aviso", "Não há gravações de Lado A ou Lado B nesta pasta.")
            return
            
        self.lbl_status.configure(text="ANALISANDO SILÊNCIO E SEPARANDO FAIXAS (ISSO PODE DEMORAR)...", text_color=COLOR_YELLOW)
        self.btn_split.configure(state="disabled")
        self.update()
        
        meta = self.meta_card.get_metadata()
        threading.Thread(target=self._process_auto_split, args=(files_to_process, meta), daemon=True).start()
        
    def _process_auto_split(self, files_to_process, meta):
        try:
            track_num = 1
            for side_name, wav_path in files_to_process:
                # 1. Carregar WAV inteiro para memoria para analise (requer RAM, mas LPs sao curtos ~25min)
                data, sr = sf.read(wav_path)
                
                # Converter para mono para analise
                if len(data.shape) > 1: mono_data = np.mean(data, axis=1)
                else: mono_data = data
                
                # Transformar em array de energia em janelas de 0.1s
                window_size = int(sr * 0.1)
                num_windows = len(mono_data) // window_size
                mono_data = mono_data[:num_windows * window_size]
                reshaped = np.abs(mono_data.reshape(num_windows, window_size))
                energy = np.mean(reshaped, axis=1)
                
                # Encontrar limites de faixas (Threshold heuristico: -45dB aprox)
                threshold = 0.005 
                is_silence = energy < threshold
                
                # Encontrar blocos continuos de não-silencio
                faixas_timestamps = []
                in_track = False
                start_w = 0
                
                # Ignorar silencios curtos (min silence = 1.5s = 15 windows)
                min_silence_windows = 15
                silence_count = 0
                
                for w, silent in enumerate(is_silence):
                    if not silent:
                        if not in_track:
                            in_track = True
                            start_w = w
                        silence_count = 0
                    else:
                        if in_track:
                            silence_count += 1
                            if silence_count >= min_silence_windows:
                                # Track ended
                                end_w = w - silence_count
                                faixas_timestamps.append((start_w * 0.1, end_w * 0.1))
                                in_track = False
                
                # Add ultima faixa se parou de gravar de repente
                if in_track:
                    faixas_timestamps.append((start_w * 0.1, len(is_silence) * 0.1))
                    
                # Processar formato
                fmt = self.opt_format.get()
                ext = ".mp3" if "MP3" in fmt else ".flac" if "FLAC" in fmt else ".wav"
                denoise = bool(self.chk_denoise.get())
                normalize = bool(self.chk_normalize.get())

                # Exportar chunks
                for start_t, end_t in faixas_timestamps:
                    duration = end_t - start_t
                    if duration < 10.0: continue # Ignora barulhos curtos perdidos
                    
                    track_meta = meta.copy()
                    track_meta["track"] = str(track_num)
                    if not track_meta.get("title"): track_meta["title"] = f"Faixa {track_num}"
                    else: track_meta["title"] = f"{track_meta['title']} {track_num}"
                        
                    out_path = os.path.join(self.project_dir, f"{track_num:02d} - {track_meta['title']}{ext}")
                    cover_path = os.path.join(self.project_dir, "cover.jpg")
                    self.engine.export_chunk(wav_path, out_path, track_meta, start_t, duration, fmt, denoise, normalize, cover_path)
                    track_num += 1

            self.after(0, lambda: self.lbl_status.configure(text=f"SUCESSO! {track_num-1} FAIXAS GERADAS.", text_color=COLOR_GREEN))
            self.after(0, lambda: messagebox.showinfo("Processo Concluído", f"Foram separadas e exportadas {track_num-1} faixas na pasta do projeto."))
        except Exception as e:
            self.after(0, lambda: self.lbl_status.configure(text="ERRO AO SEPARAR FAIXAS", text_color=COLOR_RED))
            print(e)
        finally:
            self.after(0, lambda: self.btn_split.configure(state="normal"))

    def _ui_update_loop(self):
        if hasattr(self.engine, 'latest_amp'):
            self.waveform.add_sample(self.engine.latest_amp)
        if hasattr(self.engine, 'latest_levels'):
            self.vu_in.draw(self.engine.latest_levels[0], self.engine.latest_levels[1])
            self.vu_out.draw(self.engine.latest_levels[2], self.engine.latest_levels[3])
        self.waveform.redraw()
        if hasattr(self, 'turntable'): self.turntable.update_rotation()
        self.after(30, self._ui_update_loop)

    def on_time_update(self, seconds):
        mins, secs = int(seconds // 60), int(seconds % 60)
        self.after(0, lambda: self.lbl_timer.configure(text=f"{mins:02d}:{secs:02d}.{int((seconds % 1)*100):02d}"))
    def on_closing(self):
        if self.engine.recording and not messagebox.askyesno("Sair", "Gravando! Fechar sem salvar?"):
            return
            
        choice = messagebox.askyesnocancel("Sair", "Deseja fechar o aplicativo?\n\n'Sim' para Fechar completamente.\n'Não' para Minimizar para a barra de tarefas.")
        if choice is None:
            return # Cancelar
        elif choice is False:
            self.iconify()
            return # Minimizar
            
        import os
        try:
            self.engine.stop_stream()
        except:
            pass
        os._exit(0)
if __name__ == "__main__":
    app = App()
    app.mainloop()
