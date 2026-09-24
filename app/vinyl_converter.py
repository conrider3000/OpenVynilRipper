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
        outdata[:] = indata * self.monitor_volume
        
        amplitude_chunk = float(np.max(np.abs(indata)))
        if getattr(self, 'magic_wait', False) and amplitude_chunk > 0.02:
            self.magic_wait = False
            self.start_recording(self.magic_path)
            if hasattr(self, 'on_magic_trigger'): self.on_magic_trigger()
            
        if self.recording and self.writer is not None:
            self.writer.write(indata)
            self.elapsed_seconds += frames / self.sample_rate
            if self.on_time_update: self.on_time_update(self.elapsed_seconds)
                
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
        self.draw(0.0, 0.0)
    def draw(self, vu_L, vu_R):
        self.delete("all")
        self._draw_led_bar(x=25, amplitude=vu_L, text="L")
        self._draw_led_bar(x=65, amplitude=vu_R, text="R")
        self.create_text(45, 12, text=self.label, fill=COLOR_TEXT2, font=(FONT_FAMILY, 8, "bold"))
    def _draw_led_bar(self, x, amplitude, text):
        segmentos, seg_h, seg_gap = 14, 4, 2
        filled = int(amplitude * segmentos * 2.5) 
        if filled > segmentos: filled = segmentos
        for i in range(segmentos):
            y_bottom = 105 - i * (seg_h + seg_gap)
            y_top = y_bottom - seg_h
            if i >= filled: cor = COLOR_SURFACE2
            elif i >= 11: cor = COLOR_RED
            elif i >= 8: cor = COLOR_YELLOW
            else: cor = COLOR_GREEN
            self.create_rectangle(x-8, y_top, x+8, y_bottom, fill=cor, outline="")
        self.create_text(x, 114, text=text, fill=COLOR_TEXT2, font=(FONT_FAMILY, 8))

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
        
        lbl_cam = customtkinter.CTkLabel(self.cam_window, text="")
        lbl_cam.pack(fill="both", expand=True)
        btn_take = customtkinter.CTkButton(self.cam_window, text="📸 CAPTURAR RÓTULO", height=40, font=FONT_BOLD, fg_color=COLOR_ACCENT, text_color="#000")
        btn_take.pack(pady=10)
        
        cap = cv2.VideoCapture(0)
        self.taking_photo = False
        
        def update_cam():
            if not self.cam_window.winfo_exists():
                cap.release()
                return
            ret, frame = cap.read()
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
                    cap.release()
                    self.cam_window.destroy()
                    self.label_path = save_path
                    self.draw_vinyl()
                    return
                
                ctk_img = customtkinter.CTkImage(light_image=img, size=(200, 200))
                lbl_cam.configure(image=ctk_img)
            self.after(30, update_cam)
            
        btn_take.configure(command=lambda: setattr(self, 'taking_photo', True))
        update_cam()
        
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
    def __init__(self, master, app=None, **kwargs):
        super().__init__(master, fg_color="transparent", **kwargs)
        self.app = app
        self.front_path = None
        self.back_path = None
        self.showing_front = True
        
        self.lbl_img = customtkinter.CTkLabel(self, text="📸 ADICIONAR CAPA", width=400, height=400, fg_color=COLOR_BG, font=FONT_TITLE, text_color=COLOR_SURFACE2)
        self.lbl_img.pack(fill="both", expand=True)
        self.lbl_img.bind("<Button-1>", self.open_camera)
        
        self.btn_flip = customtkinter.CTkButton(self, text="🔄 Virar Capa", width=200, height=24, fg_color=COLOR_SURFACE2, font=FONT_MAIN, command=self.flip_cover, state="disabled")
        self.btn_flip.pack(pady=(5, 0))
        
    def open_camera(self, event=None):
        import cv2, threading
        from PIL import Image, ImageTk
        if hasattr(self, 'cam_window') and self.cam_window.winfo_exists():
            return
            
        self.cam_window = customtkinter.CTkToplevel(self)
        self.cam_window.title("Tirar Foto da Capa")
        self.cam_window.geometry("500x550")
        self.cam_window.attributes("-topmost", True)
        
        lbl_cam = customtkinter.CTkLabel(self.cam_window, text="")
        lbl_cam.pack(fill="both", expand=True)
        
        btn_take = customtkinter.CTkButton(self.cam_window, text="📸 CAPTURAR", height=40, font=FONT_BOLD, fg_color=COLOR_ACCENT, text_color="#000")
        btn_take.pack(pady=10)
        
        cap = cv2.VideoCapture(0)
        self.taking_photo = False
        
        def update_cam():
            if not self.cam_window.winfo_exists():
                cap.release()
                return
            ret, frame = cap.read()
            if ret:
                frame = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
                # Crop to square
                h, w, _ = frame.shape
                min_dim = min(h, w)
                sx, sy = (w - min_dim) // 2, (h - min_dim) // 2
                frame_sq = frame[sy:sy+min_dim, sx:sx+min_dim]
                img = Image.fromarray(frame_sq).resize((400, 400))
                
                if self.taking_photo:
                    save_path = os.path.join(self.app.project_dir, "cover.jpg")
                    img.save(save_path, quality=90)
                    cap.release()
                    self.cam_window.destroy()
                    self.load_covers(save_path, self.back_path)
                    return
                
                ctk_img = customtkinter.CTkImage(light_image=img, size=(400, 400))
                lbl_cam.configure(image=ctk_img)
            self.after(30, update_cam)
            
        btn_take.configure(command=lambda: setattr(self, 'taking_photo', True))
        update_cam()
        
    def load_covers(self, front_path, back_path):
        self.front_path = front_path
        self.back_path = back_path
        self.showing_front = True
        self.btn_flip.configure(state="normal" if back_path and os.path.exists(back_path) else "disabled")
        self._update_img()
        
    def flip_cover(self):
        self.showing_front = not self.showing_front
        self._update_img()
        
    def _update_img(self):
        from PIL import Image
        path = self.front_path if self.showing_front else self.back_path
        if path and os.path.exists(path):
            img = Image.open(path)
            # Make sure it's big!
            w = self.winfo_width() if self.winfo_width() > 10 else 400
            ctk_img = customtkinter.CTkImage(light_image=img, size=(w, w))
            self.lbl_img.configure(image=ctk_img, text="")
        else:
            self.lbl_img.configure(image="", text="📸 ADICIONAR CAPA")


CARTRIDGES = [
    "Audio-Technica AT95E", "Audio-Technica AT-VM95E", "Audio-Technica AT-VM95ML", "Audio-Technica AT-VM95SH",
    "Ortofon 2M Red", "Ortofon 2M Blue", "Ortofon 2M Bronze", "Ortofon 2M Black", "Ortofon OM5E", "Ortofon OM10",
    "Shure M44-7", "Shure M97xE", "Shure V15 Type III", "Shure V15 Type IV",
    "Nagaoka MP-110", "Nagaoka MP-150", "Nagaoka MP-200",
    "Goldring E3", "Goldring 1042", "Rega Carbon", "Rega Elys 2", "Rega Exact",
    "Denon DL-103", "Denon DL-110", "Sumiko Pearl", "Sumiko Moonstone",
    "Grado Prestige Green", "Grado Prestige Gold", "Clearaudio Concept V2", 
    "ION (Agulha Cerâmica Padrão)", "ION (Agulha Safira/Rubi)"
]

class MetadataCard(customtkinter.CTkFrame):
    def __init__(self, master, on_album_found=None, **kwargs):
        super().__init__(master, fg_color=COLOR_SURFACE, corner_radius=0, border_width=1, border_color=COLOR_SURFACE2, **kwargs)
        self.on_album_found = on_album_found
        
        top_frame = customtkinter.CTkFrame(self, fg_color="transparent")
        top_frame.pack(fill="x", pady=(10, 5), padx=15)
        
        lbl_title = customtkinter.CTkLabel(top_frame, text="[ METADADOS DO ÁLBUM ]", font=FONT_BOLD, text_color=COLOR_TEXT2)
        lbl_title.pack(side="left")
        
        btn_search = customtkinter.CTkButton(top_frame, text="🔍 Buscar Álbum", width=120, height=24, fg_color=COLOR_SURFACE2, font=FONT_MAIN, command=self.search_album)
        btn_search.pack(side="right")
        
        self.entries = {}
        fields = [("Artista / Banda", "artist"), ("Álbum", "album"), ("Ano", "year"), ("Agulha/Cápsula", "cartridge"), ("Vinil (ex: 180g)", "vinyl_spec")]
        for label_text, key in fields:
            f = customtkinter.CTkFrame(self, fg_color="transparent")
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
            
    def _filter_cartridges(self, event):
        typed = self.entries["cartridge"].get().lower()
        hits = [c for c in CARTRIDGES if typed in c.lower()]
        self.entries["cartridge"].configure(values=hits if hits else CARTRIDGES)
        
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
        
        main_container = customtkinter.CTkFrame(self, fg_color="transparent")
        main_container.pack(fill="both", expand=True, padx=10, pady=5)
        
        left_col = customtkinter.CTkFrame(main_container, fg_color="transparent")
        left_col.pack(side="left", fill="both", expand=True, padx=(0, 5))
        
        right_col = customtkinter.CTkFrame(main_container, fg_color="transparent")
        right_col.pack(side="right", fill="both", expand=True, padx=(5, 0))
        
        dev_frame = customtkinter.CTkFrame(left_col, fg_color=COLOR_SURFACE, corner_radius=0, border_width=1, border_color=COLOR_SURFACE2)
        dev_frame.pack(fill="x")
        
        dev_top = customtkinter.CTkFrame(dev_frame, fg_color="transparent")
        dev_top.pack(fill="x", pady=(10, 5), padx=15)
        customtkinter.CTkLabel(dev_top, text="[ ROTEAMENTO ]", font=FONT_BOLD, text_color=COLOR_TEXT2).pack(side="left")
        customtkinter.CTkButton(dev_top, text="🔄 Atualizar USB", width=80, height=24, fg_color=COLOR_SURFACE2, font=FONT_MAIN, command=self._init_devices).pack(side="right")
        
        self.opt_in = customtkinter.CTkOptionMenu(dev_frame, values=["Nenhum"], font=FONT_MAIN, fg_color=COLOR_BG, button_color=COLOR_SURFACE2, text_color=COLOR_TEXT, command=self._on_device_change)
        self.opt_in.pack(fill="x", padx=15, pady=5)
        self.opt_out = customtkinter.CTkOptionMenu(dev_frame, values=["Nenhum"], font=FONT_MAIN, fg_color=COLOR_BG, button_color=COLOR_SURFACE2, text_color=COLOR_TEXT, command=self._on_device_change)
        self.opt_out.pack(fill="x", padx=15, pady=5)
        
        vol_frame = customtkinter.CTkFrame(dev_frame, fg_color="transparent")
        vol_frame.pack(fill="x", padx=15, pady=15)
        customtkinter.CTkLabel(vol_frame, text="VOL. MONITOR:", font=FONT_MAIN, text_color=COLOR_TEXT).pack(side="left")
        self.vol_slider = customtkinter.CTkSlider(vol_frame, from_=0, to=1.5, button_color=COLOR_ACCENT, progress_color=COLOR_ACCENT, command=self._on_volume_change)
        self.vol_slider.set(1.0)
        self.vol_slider.pack(side="right", fill="x", expand=True, padx=(10, 0))
        
        self.turntable = VirtualTurntable(left_col)
        self.turntable.pack(fill="both", expand=True, pady=(10,0))
        
        self.meta_card = MetadataCard(right_col, on_album_found=self._fetch_cover_art)
        self.meta_card.pack(fill="x")
        
        self.cover_display = CoverDisplay(right_col, app=self)
        self.cover_display.pack(fill="both", expand=True, pady=(10,0))
        
        vis_frame = customtkinter.CTkFrame(self, fg_color=COLOR_SURFACE, corner_radius=0, border_width=1, border_color=COLOR_SURFACE2)
        vis_frame.pack(fill="x", padx=10, pady=(5, 0))
        self.vu_in = AnalogVUMeter(vis_frame, label="INPUT VU")
        self.vu_in.pack(side="left", padx=15, pady=15)
        self.waveform = RetroWaveform(vis_frame)
        self.waveform.pack(side="left", expand=True, fill="both", pady=15)
        self.vu_out = AnalogVUMeter(vis_frame, label="MONITOR VU")
        self.vu_out.pack(side="right", padx=15, pady=15)
        
        # TRANSPORT & PROJECT SECTION
        proj_frame = customtkinter.CTkFrame(self, fg_color=COLOR_SURFACE, corner_radius=0, border_width=1, border_color=COLOR_SURFACE2)
        proj_frame.pack(fill="x", padx=10, pady=5)
        
        p_top = customtkinter.CTkFrame(proj_frame, fg_color="transparent")
        p_top.pack(fill="x", padx=15, pady=10)
        customtkinter.CTkLabel(p_top, text="PASTA DO DISCO:", font=FONT_BOLD, text_color=COLOR_TEXT2).pack(side="left")
        self.lbl_folder = customtkinter.CTkLabel(p_top, text=self.project_dir, font=FONT_MAIN, text_color=COLOR_TEXT)
        self.lbl_folder.pack(side="left", padx=15)
        customtkinter.CTkButton(p_top, text="ALTERAR PASTA", font=FONT_BOLD, fg_color=COLOR_SURFACE2, width=120, command=self._choose_folder).pack(side="right")
        
        p_mid = customtkinter.CTkFrame(proj_frame, fg_color="transparent")
        p_mid.pack(fill="x", padx=15, pady=5)
        
        self.lbl_timer = customtkinter.CTkLabel(p_mid, text="00:00.00", font=(FONT_FAMILY, 28, "bold"), text_color=COLOR_RED)
        self.lbl_timer.pack(side="left", padx=(0, 20))
        
        self.btn_rec_a = customtkinter.CTkButton(p_mid, text="● GRAVAR LADO A", fg_color=COLOR_RED, text_color="#FFF", font=FONT_BOLD, height=40, command=lambda: self.on_rec_side("LadoA"))
        self.btn_rec_a.pack(side="left", padx=5)
        self.btn_rec_b = customtkinter.CTkButton(p_mid, text="● GRAVAR LADO B", fg_color=COLOR_RED, text_color="#FFF", font=FONT_BOLD, height=40, command=lambda: self.on_rec_side("LadoB"))
        self.btn_rec_b.pack(side="left", padx=5)
        self.btn_stop = customtkinter.CTkButton(p_mid, text="■ STOP", fg_color=COLOR_SURFACE2, text_color=COLOR_TEXT, font=FONT_BOLD, height=40, state="disabled", command=self.on_stop_click)
        self.btn_stop.pack(side="left", padx=5)
        
        self.chk_magic = customtkinter.CTkCheckBox(p_mid, text="🪄 Magic Record (Início Automático)", fg_color="#8a2be2", text_color=COLOR_TEXT, font=FONT_MAIN)
        self.chk_magic.pack(side="left", padx=15)
        
        p_bot = customtkinter.CTkFrame(proj_frame, fg_color="transparent")
        p_bot.pack(fill="x", padx=15, pady=10)
        
        opt_frame = customtkinter.CTkFrame(p_bot, fg_color="transparent")
        opt_frame.pack(fill="x", pady=(0, 10))
        
        customtkinter.CTkLabel(opt_frame, text="Exportar em:", font=FONT_BOLD, text_color=COLOR_TEXT2).pack(side="left")
        self.opt_format = customtkinter.CTkOptionMenu(opt_frame, values=["MP3 (Padrão)", "FLAC (Lossless)", "WAV (Original)"], fg_color=COLOR_BG, button_color=COLOR_SURFACE2, font=FONT_MAIN)
        self.opt_format.pack(side="left", padx=10)
        
        self.chk_normalize = customtkinter.CTkCheckBox(opt_frame, text="Normalizar Vol.", fg_color=COLOR_ACCENT, text_color=COLOR_TEXT, font=FONT_MAIN)
        self.chk_normalize.pack(side="left", padx=10)
        
        self.chk_denoise = customtkinter.CTkCheckBox(opt_frame, text="Filtro Anti-Chiado", fg_color=COLOR_ACCENT, text_color=COLOR_TEXT, font=FONT_MAIN)
        self.chk_denoise.pack(side="right")
        
        self.btn_split = customtkinter.CTkButton(p_bot, text="✂ SEPARAR FAIXAS AUTOMÁTICO E EXPORTAR", fg_color=COLOR_ACCENT, text_color="#000", font=FONT_BOLD, height=40, command=self.on_auto_split)
        self.btn_split.pack(fill="x")
        
        self.lbl_status = customtkinter.CTkLabel(self, text="SISTEMA PRONTO", font=FONT_MAIN, text_color=COLOR_GREEN)
        self.lbl_status.pack(pady=5)

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
                        
                self.after(0, lambda: self.cover_display.load_covers(front_path, back_path))
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
        import os
        try:
            self.engine.stop_stream()
        except:
            pass
        os._exit(0)

if __name__ == "__main__":
    app = App()
    app.mainloop()
