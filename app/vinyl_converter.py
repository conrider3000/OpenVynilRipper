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

# --- VINTAGE / ANALOG THEMES (NIGHT & DAY) ---
THEMES = {
    "dark": {
        "bg": "#121212",        # Chassi escuro
        "surface": "#1E1E1E",   # Painéis
        "surface2": "#282828",  # Botões e fundos de input
        "border": "#333333",    # Bordas
        "accent": "#D87D00",    # Laranja/Âmbar (vintage)
        "text": "#FFB84D",      # Texto luminoso estilo painel analógico
        "text2": "#D6A354",     # Texto secundário nítido
        "text3": "#8C6A38",     # Texto terciário
        "red": "#E63900",       # Clip do VU / Gravação
        "yellow": "#FFB300",    # Médio do VU
        "green": "#85C733",     # Baixo do VU
        "ctk_mode": "dark"
    },
    "light": {
        "bg": "#F5F2EB",        # Chassi Off-White vintage suave
        "surface": "#EAE4D7",   # Painéis bege/creme clássico
        "surface2": "#DDD5C4",  # Botões e caixas de input
        "border": "#C7BEAB",    # Bordas suaves
        "accent": "#D87D00",    # Laranja vintage
        "text": "#2B2115",      # Texto escuro contrastante
        "text2": "#634E35",     # Texto secundário legível
        "text3": "#8A7456",     # Texto terciário
        "red": "#D12A00",
        "yellow": "#D48B00",
        "green": "#4F8C18",
        "ctk_mode": "light"
    }
}

CURRENT_THEME = "dark"
COLOR_BG         = THEMES[CURRENT_THEME]["bg"]
COLOR_SURFACE    = THEMES[CURRENT_THEME]["surface"]
COLOR_SURFACE2   = THEMES[CURRENT_THEME]["surface2"]
COLOR_BORDER     = THEMES[CURRENT_THEME]["border"]
COLOR_ACCENT     = THEMES[CURRENT_THEME]["accent"]
COLOR_TEXT       = THEMES[CURRENT_THEME]["text"]
COLOR_TEXT2      = THEMES[CURRENT_THEME]["text2"]
COLOR_TEXT3      = THEMES[CURRENT_THEME]["text3"]
COLOR_RED        = THEMES[CURRENT_THEME]["red"]
COLOR_YELLOW     = THEMES[CURRENT_THEME]["yellow"]
COLOR_GREEN      = THEMES[CURRENT_THEME]["green"]

FONT_FAMILY = "Consolas" if sys.platform == "win32" else "Menlo"
FONT_MAIN = (FONT_FAMILY, 12)
FONT_LABEL = (FONT_FAMILY, 12, "bold") # Legendas legíveis e nítidas
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
        self.writer_lock = __import__('threading').Lock()
        
        self.waveform_buffer = collections.deque(maxlen=400)
        self.elapsed_seconds = 0.0
        self.on_level_update = None
        self.on_time_update = None
        self.passthrough_mode = False
        self.passthrough_running = False
        self.passthrough_thread = None
        
        # Equalizador de 3 Bandas (Grave, Médio, Agudo) - neutro = 1.0 (0dB)
        self.eq_bass = 1.0
        self.eq_mid = 1.0
        self.eq_treble = 1.0
        self.muted = False
        self._precompute_eq_filters()

    def _precompute_eq_filters(self):
        freqs = np.fft.rfftfreq(self.blocksize, 1.0 / self.sample_rate)
        # Curvas com transições suaves (filtros analógicos gaussianos)
        self._bass_mask = np.exp(-0.5 * (freqs / 250.0)**2)
        self._treble_mask = 1.0 - np.exp(-0.5 * (freqs / 3500.0)**2)
        self._mid_mask = np.maximum(0.0, 1.0 - self._bass_mask - self._treble_mask)

    def apply_eq(self, data):
        # Se todos estiverem flat (1.0), pula processamento para zero overhead
        if abs(self.eq_bass - 1.0) < 0.02 and abs(self.eq_mid - 1.0) < 0.02 and abs(self.eq_treble - 1.0) < 0.02:
            return data
            
        if len(data) != self.blocksize:
            freqs = np.fft.rfftfreq(len(data), 1.0 / self.sample_rate)
            b_mask = np.exp(-0.5 * (freqs / 250.0)**2)
            t_mask = 1.0 - np.exp(-0.5 * (freqs / 3500.0)**2)
            m_mask = np.maximum(0.0, 1.0 - b_mask - t_mask)
            eq_curve = (self.eq_bass * b_mask + self.eq_mid * m_mask + self.eq_treble * t_mask)[:, np.newaxis]
        else:
            eq_curve = (self.eq_bass * self._bass_mask + self.eq_mid * self._mid_mask + self.eq_treble * self._treble_mask)[:, np.newaxis]

        spec = np.fft.rfft(data, axis=0)
        spec *= eq_curve
        return np.fft.irfft(spec, axis=0).astype(np.float32)

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
                if dev['max_input_channels'] >= 1:
                    display_name = "🎧 TOCA-DISCOS ION (Entrada Vinil)"
                else:
                    display_name = "🔊 TOCA-DISCOS ION (Saída Caixas)"

            if dev['max_input_channels'] >= 1 and display_name not in inputs:
                inputs[display_name] = {"index": i, "name": display_name}
            if dev['max_output_channels'] >= 1 and display_name not in outputs:
                outputs[display_name] = {"index": i, "name": display_name}
                
        return {"inputs": list(inputs.values()), "outputs": list(outputs.values())}

    def start_stream(self):
        if self.passthrough_mode:
            self._start_passthrough_stream()
            return

        if self.input_device is None or self.output_device is None: return
        try:
            self.stream = sd.Stream(
                device=(self.input_device, self.output_device), samplerate=self.sample_rate,
                channels=self.channels, dtype='float32', blocksize=self.blocksize,
                callback=self._audio_callback, latency='low'
            )
            self.stream.start()
        except: self.stream = None

    def _start_passthrough_stream(self):
        self.stop_stream()
        self.passthrough_running = True
        self.passthrough_thread = threading.Thread(target=self._passthrough_worker, daemon=True)
        self.passthrough_thread.start()

    def _passthrough_worker(self):
        import soundcard as sc
        out_dev = self.output_device
        if out_dev is None:
            # Fallback para o toca-discos se houver
            devs = self.get_devices()
            for d in devs["outputs"]:
                if "ION" in d["name"].upper() or "USB" in d["name"].upper():
                    out_dev = d["index"]
                    break
        if out_dev is None:
            out_dev = sd.default.device[1]

        try:
            # Pega o autofalante de reprodução padrão do Windows ou o monitor do sistema para loopback
            speakers = sc.all_speakers()
            target_spk = sc.default_speaker()
            for spk in speakers:
                # Se o speaker padrão for o próprio toca-discos, pega os alto-falantes internos do PC
                if "USB" not in spk.name.upper():
                    target_spk = spk
                    break

            loop_mic = sc.get_microphone(id=str(target_spk.name), include_loopback=True)
            out_stream = sd.OutputStream(
                device=out_dev,
                channels=self.channels,
                samplerate=self.sample_rate,
                blocksize=self.blocksize,
                dtype='float32'
            )
            out_stream.start()
            self.stream = out_stream

            with loop_mic.recorder(samplerate=self.sample_rate, blocksize=self.blocksize) as rec:
                while self.passthrough_running:
                    if self.paused:
                        silence = np.zeros((self.blocksize, self.channels), dtype=np.float32)
                        out_stream.write(silence)
                        continue

                    data = rec.record(numframes=self.blocksize)
                    if data.shape[1] > 2:
                        data = data[:, :2]
                    elif data.shape[1] == 1:
                        data = np.column_stack((data, data))

                    if getattr(self, 'muted', False):
                        data = np.zeros_like(data)

                    # Aplica equalização (Grave, Médio, Agudo) e ganhos dos knobs
                    data = self.apply_eq(data)
                    data = data * self.input_gain * self.monitor_volume
                    c_data = np.ascontiguousarray(data, dtype=np.float32)
                    out_stream.write(c_data)

                    # Atualiza VU e Waveform
                    amp = float(np.max(np.abs(c_data)))
                    vu_L = float(np.sqrt(np.mean(c_data[:, 0]**2)))
                    vu_R = float(np.sqrt(np.mean(c_data[:, 1]**2)))
                    self.waveform_buffer.append(amp)
                    self.latest_levels = (vu_L, vu_R, vu_L, vu_R)
                    self.latest_amp = amp

        except Exception as e:
            print("Passthrough error:", e)
        finally:
            if 'out_stream' in locals():
                try:
                    out_stream.stop()
                    out_stream.close()
                except:
                    pass
            self.stream = None
            self.passthrough_running = False

    def _audio_callback(self, indata, outdata, frames, time_info, status):
        if getattr(self, 'muted', False):
            indata = np.zeros_like(indata)

        if self.paused:
            outdata[:] = np.zeros_like(outdata)
            return
            
        indata = indata * self.input_gain
        # Aplica equalização no monitor das caixas/fones
        processed_monitor = self.apply_eq(indata) * self.monitor_volume
        outdata[:] = processed_monitor
        
        amplitude_chunk = float(np.max(np.abs(indata)))
        if getattr(self, 'magic_wait', False) and amplitude_chunk > 0.02:
            self.magic_wait = False
            self.start_recording(self.magic_path)
            if hasattr(self, 'on_magic_trigger'): self.on_magic_trigger()
            
        if self.recording:
            with self.writer_lock:
                if self.writer is not None:
                    try:
                        self.writer.write(indata)
                        self.elapsed_seconds += frames / self.sample_rate
                    except Exception as e:
                        print('Audio write error:', e)
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
        with self.writer_lock:
            self.writer = sf.SoundFile(
                output_path, mode='w', samplerate=self.sample_rate, channels=self.channels, format='WAV', subtype='PCM_16'
            )
        self.recording = True

    def stop_recording(self) -> str:
        self.recording = False
        with self.writer_lock:
            if self.writer:
                try:
                    self.writer.close()
                except:
                    pass
                self.writer = None
        return self.output_path

    def pause(self): self.paused = True
    def resume(self): self.paused = False
    def stop_stream(self):
        self.passthrough_running = False
        if self.stream is not None:
            try:
                if hasattr(self.stream, 'abort'):
                    self.stream.abort()
                if hasattr(self.stream, 'stop'):
                    self.stream.stop()
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
class RotaryKnob(customtkinter.CTkFrame):
    def __init__(self, master, label="KNOB", command=None, init_val=1.0, max_val=2.0, **kwargs):
        super().__init__(master, fg_color="transparent", **kwargs)
        self.command = command
        self.max_val = max_val
        self.val = init_val
        import math
        self.math = math
        self.canvas = customtkinter.CTkCanvas(self, width=50, height=50, bg=COLOR_BG, highlightthickness=0)
        self.canvas.pack(pady=(0, 2))
        self.lbl = customtkinter.CTkLabel(self, text=label, font=(FONT_FAMILY, 10, "bold"), text_color=COLOR_TEXT2)
        self.lbl.pack()
        self.canvas.bind("<B1-Motion>", self.on_drag)
        self.canvas.bind("<Button-1>", self.on_click)
        self.draw()

    def on_click(self, event): self.start_y = event.y

    def on_drag(self, event):
        dy = self.start_y - event.y
        self.start_y = event.y
        self.val = max(0.0, min(self.max_val, self.val + dy * 0.02))
        self.draw()
        if self.command: self.command(self.val)

    def draw(self):
        self.canvas.delete("all")
        cx, cy, r = 25, 25, 18
        self.canvas.create_oval(cx-r, cy-r, cx+r, cy+r, fill=COLOR_SURFACE2, outline=COLOR_BORDER, width=2)
        angle = 135 + (self.val / self.max_val) * 270
        rad = self.math.radians(angle)
        lx = cx + (r-4) * self.math.cos(rad)
        ly = cy + (r-4) * self.math.sin(rad)
        self.canvas.create_line(cx, cy, lx, ly, fill=COLOR_ACCENT, width=3)

    def update_theme(self):
        self.canvas.configure(bg=COLOR_BG)
        self.lbl.configure(text_color=COLOR_TEXT2)
        self.draw()

class RetroWaveform(customtkinter.CTkFrame):
    def __init__(self, master, **kwargs):
        super().__init__(master, fg_color=COLOR_SURFACE, corner_radius=8, border_width=2, border_color=COLOR_BORDER, height=140, **kwargs)
        self.pack_propagate(False)
        self.history = collections.deque(maxlen=400)
        self.style = 0
        self.canvas = tk.Canvas(self, bg=COLOR_BG, highlightthickness=0)
        self.canvas.pack(fill="both", expand=True, padx=4, pady=4)
        self.canvas.bind("<Button-1>", self.toggle_style)
        
    def toggle_style(self, event):
        self.style = (self.style + 1) % 3
        self.redraw()
        
    def add_sample(self, amplitude: float):
        self.history.append(amplitude)
        
    def redraw(self):
        self.canvas.delete("all")
        w = self.canvas.winfo_width()
        h = self.canvas.winfo_height()
        if w < 10 or h < 10: return
        
        mid_y = h / 2
        step = w / 400.0 if self.history else 1
        
        for i, amp in enumerate(self.history):
            x = int(i * step)
            ah = int(amp * (h / 2 - 5))
            
            if self.style == 0:
                self.canvas.create_line(x, mid_y - ah, x, mid_y + ah, fill=COLOR_ACCENT, width=2 if step > 2 else 1)
            elif self.style == 1:
                self.canvas.create_rectangle(x, mid_y - ah, x + max(1, int(step)), mid_y + ah, fill=COLOR_TEXT, outline="")
            else:
                self.canvas.create_oval(x, mid_y - ah - 2, x + 2, mid_y - ah, fill=COLOR_RED, outline="")
                self.canvas.create_oval(x, mid_y + ah, x + 2, mid_y + ah + 2, fill=COLOR_RED, outline="")
                self.canvas.create_line(x, mid_y, x + 2, mid_y, fill=COLOR_ACCENT)
                
        self.canvas.create_line(0, mid_y, w, mid_y, fill=COLOR_SURFACE2, dash=(2, 2))
        self.canvas.create_text(w - 60, 15, text="[MUDAR ANIMAÇÃO]", fill=COLOR_TEXT2, font=(FONT_FAMILY, 8, "bold"))

    def update_theme(self):
        self.configure(fg_color=COLOR_SURFACE, border_color=COLOR_BORDER)
        self.canvas.configure(bg=COLOR_BG)
        self.redraw()

class AnalogVUMeter(tk.Canvas):
    def __init__(self, master, label="VU", **kwargs):
        super().__init__(master, bg=COLOR_BG, highlightthickness=1, highlightbackground=COLOR_BORDER, width=90, height=120, **kwargs)
        self.label = label
        self.style = 0
        self.bind("<Button-1>", self.toggle_style)
        self.draw(0.0, 0.0)
        
    def toggle_style(self, event=None):
        self.style = (self.style + 1) % 3
        self.draw(0.0, 0.0)

    def update_theme(self):
        self.configure(bg=COLOR_BG, highlightbackground=COLOR_BORDER)
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
        super().__init__(master, fg_color="transparent", width=150, height=150, **kwargs)
        self.pack_propagate(False)
        self.app = app
        self.angle = 0
        self.is_playing = False
        self.label_path = None
        self.tk_label_img = None
        self.hover_active = False
        
        self.canvas = customtkinter.CTkCanvas(self, bg=COLOR_BG, highlightthickness=0)
        self.canvas.pack(fill="both", expand=True)
        self.canvas.bind("<Configure>", lambda e: self.draw_vinyl())
        self.canvas.bind("<Button-1>", self.show_image_options)
        self.canvas.bind("<Motion>", self.on_motion)
        self.canvas.bind("<Leave>", self.on_leave)
        
    def on_motion(self, event):
        if getattr(self, 'camera_active', False): return
        w = self.canvas.winfo_width()
        h = self.canvas.winfo_height()
        cx, cy = w / 2, h / 2
        r = min(w, h) / 2 - 10
        label_r = r * 0.35
        dist = ((event.x - cx)**2 + (event.y - cy)**2)**0.5
        hover = dist <= label_r
        if hover != self.hover_active:
            self.hover_active = hover
            self.draw_vinyl()
            
    def on_leave(self, event):
        if getattr(self, 'camera_active', False): return
        if self.hover_active:
            self.hover_active = False
            self.draw_vinyl()

    def show_image_options(self, event=None):
        if event and not getattr(self, 'hover_active', False):
            return
        if getattr(self, 'camera_active', False):
            return
        if not self.app:
            return

        dlg = customtkinter.CTkToplevel(self)
        dlg.title("Imagem do Rótulo")
        dlg.geometry("340x200")
        dlg.resizable(False, False)
        dlg.attributes("-topmost", True)
        dlg.configure(fg_color=COLOR_BG)
        
        try:
            x = self.winfo_rootx() + (self.winfo_width() // 2) - 170
            y = self.winfo_rooty() + (self.winfo_height() // 2) - 100
            dlg.geometry(f"+{x}+{y}")
        except:
            pass

        lbl = customtkinter.CTkLabel(dlg, text="Alterar Rótulo Central", font=FONT_TITLE, text_color=COLOR_ACCENT)
        lbl.pack(pady=(18, 12))

        def on_cam():
            dlg.destroy()
            self.start_in_place_camera()

        def on_pc():
            dlg.destroy()
            self.load_from_pc()

        btn_cam = customtkinter.CTkButton(
            dlg, text="📸 Tirar Foto com a Câmera", height=36,
            font=FONT_BOLD, fg_color=COLOR_ACCENT, text_color="#000",
            hover_color=COLOR_BORDER, corner_radius=6, command=on_cam
        )
        btn_cam.pack(fill="x", padx=25, pady=4)

        btn_pc = customtkinter.CTkButton(
            dlg, text="📁 Carregar Imagem do PC", height=36,
            font=FONT_BOLD, fg_color=COLOR_SURFACE2, text_color=COLOR_TEXT,
            hover_color=COLOR_BORDER, corner_radius=6, command=on_pc
        )
        btn_pc.pack(fill="x", padx=25, pady=4)

    def load_from_pc(self):
        from tkinter import filedialog
        import shutil
        from PIL import Image
        path = filedialog.askopenfilename(title="Escolha a imagem do rótulo", filetypes=[("Imagens", "*.jpg *.jpeg *.png")])
        if path:
            save_path = os.path.join(self.app.project_dir, "label.jpg")
            try:
                img = Image.open(path).convert("RGB")
                w, h = img.size
                min_dim = min(h, w)
                sx, sy = (w - min_dim) // 2, (h - min_dim) // 2
                img = img.crop((sx, sy, sx+min_dim, sy+min_dim))
                img.save(save_path, quality=92)
            except:
                shutil.copy(path, save_path)
            self.label_path = save_path
            self.draw_vinyl()

    def start_in_place_camera(self):
        if getattr(self, 'camera_active', False):
            return
        import cv2
        self.cap = cv2.VideoCapture(0)
        if not self.cap or not self.cap.isOpened():
            messagebox.showwarning("Câmera", "Não foi possível acessar a câmera do computador.")
            if self.cap: self.cap.release()
            self.cap = None
            return

        self.camera_active = True
        self._latest_cam_frame = None

        self.cam_controls = customtkinter.CTkFrame(self, fg_color=COLOR_SURFACE, height=36, corner_radius=6)
        self.cam_controls.place(relx=0.04, rely=0.82, relwidth=0.92, height=34)

        self.btn_capture = customtkinter.CTkButton(
            self.cam_controls, text="📸 CAPTURAR", height=26,
            fg_color=COLOR_ACCENT, text_color="#000", font=FONT_BOLD,
            command=self.capture_photo
        )
        self.btn_capture.pack(side="left", fill="x", expand=True, padx=(4, 2), pady=4)

        self.btn_cancel_cam = customtkinter.CTkButton(
            self.cam_controls, text="❌ CANCELAR", height=26,
            fg_color=COLOR_SURFACE2, text_color=COLOR_TEXT, font=FONT_MAIN,
            command=self.stop_in_place_camera
        )
        self.btn_cancel_cam.pack(side="left", fill="x", expand=True, padx=(2, 4), pady=4)

        self._stream_camera()

    def _stream_camera(self):
        if not getattr(self, 'camera_active', False) or not self.cap:
            return
        import cv2
        from PIL import Image, ImageTk, ImageDraw

        ret, frame = self.cap.read()
        if ret:
            # Espelhamento horizontal para movimento natural
            frame = cv2.flip(frame, 1)
            frame = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            h, w, _ = frame.shape
            min_dim = min(h, w)
            sx, sy = (w - min_dim) // 2, (h - min_dim) // 2
            frame_sq = frame[sy:sy+min_dim, sx:sx+min_dim]
            self._latest_cam_frame = Image.fromarray(frame_sq)

            cw = self.canvas.winfo_width()
            ch = self.canvas.winfo_height()
            cx, cy = cw // 2, ch // 2
            r = min(cw, ch) // 2 - 10
            label_r = int(r * 0.7)

            preview_img = self._latest_cam_frame.resize((label_r * 2, label_r * 2), Image.Resampling.BILINEAR)
            mask = Image.new('L', (label_r * 2, label_r * 2), 0)
            draw = ImageDraw.Draw(mask)
            draw.ellipse((0, 0, label_r * 2, label_r * 2), fill=255)
            preview_img.putalpha(mask)

            self.tk_cam_img = ImageTk.PhotoImage(preview_img)
            self.canvas.delete("all")
            self.canvas.create_oval(cx-r, cy-r, cx+r, cy+r, fill="#111", outline="#333", width=2)
            self.canvas.create_image(cx, cy, image=self.tk_cam_img)
            self.canvas.create_oval(cx-label_r, cy-label_r, cx+label_r, cy+label_r, outline=COLOR_ACCENT, width=2)
            self.canvas.create_oval(cx-5, cy-5, cx+5, cy+5, fill=COLOR_BG, outline="")

        self.after(30, self._stream_camera)

    def capture_photo(self):
        if hasattr(self, '_latest_cam_frame') and self._latest_cam_frame:
            save_path = os.path.join(self.app.project_dir, "label.jpg")
            try:
                self._latest_cam_frame.save(save_path, quality=92)
            except Exception as e:
                print("Error saving label photo:", e)
            self.stop_in_place_camera()
            self.label_path = save_path
            self.draw_vinyl()
        else:
            self.stop_in_place_camera()

    def stop_in_place_camera(self):
        self.camera_active = False
        if hasattr(self, 'cap') and self.cap:
            try:
                self.cap.release()
            except:
                pass
            self.cap = None
        if hasattr(self, 'cam_controls') and self.cam_controls and self.cam_controls.winfo_exists():
            self.cam_controls.destroy()
            self.cam_controls = None
        self._latest_cam_frame = None
        self.draw_vinyl()
        
    def set_playing(self, playing: bool):
        self.is_playing = playing
        
    def update_rotation(self):
        if self.is_playing:
            self.angle = (self.angle + 5) % 360
            self.draw_vinyl()
        
    def draw_vinyl(self):
        self.canvas.delete("all")
        w = self.canvas.winfo_width()
        h = self.canvas.winfo_height()
        if w < 10 or h < 10: return
        
        cx, cy = w // 2, h // 2
        r = (min(w, h) // 2) - 10
        if r < 10: return
        
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

    def update_theme(self):
        self.canvas.configure(bg=COLOR_BG)
        self.draw_vinyl()


class CoverDisplay(customtkinter.CTkFrame):
    def __init__(self, master, app=None, title="CAPA", **kwargs):
        super().__init__(master, fg_color="transparent", width=150, height=150, **kwargs)
        self.pack_propagate(False)
        self.app = app
        self.title_text = title
        self.cover_path = None
        self.original_img = None
        self.hover_active = False
        self._cached_dim = 0
        self._cached_normal = None
        self._cached_hover = None
        
        self.canvas = customtkinter.CTkCanvas(self, bg=COLOR_BG, highlightthickness=0)
        self.canvas.pack(fill="both", expand=True, padx=2, pady=2)
        self.canvas.bind("<Button-1>", self.show_image_options)
        self.canvas.bind("<Enter>", self.on_hover)
        self.canvas.bind("<Leave>", self.on_leave)
        self.canvas.bind("<Configure>", lambda e: self.redraw())

    def update_theme(self):
        self.canvas.configure(bg=COLOR_BG)
        self.redraw()

    def on_hover(self, event):
        if getattr(self, 'camera_active', False): return
        self.hover_active = True
        self.redraw()
            
    def on_leave(self, event):
        if getattr(self, 'camera_active', False): return
        self.hover_active = False
        self.redraw()

    def redraw(self):
        if getattr(self, 'camera_active', False): return
        self.canvas.delete("all")
        w = self.canvas.winfo_width()
        h = self.canvas.winfo_height()
        if w < 10 or h < 10: return
        
        dim = max(10, min(w, h) - 10)
        cx, cy = w // 2, h // 2
        
        if not self.original_img:
            self.canvas.create_rectangle(cx - dim//2, cy - dim//2, cx + dim//2, cy + dim//2, fill=COLOR_SURFACE, outline=COLOR_BORDER, width=2)
            self.canvas.create_text(cx, cy, text=self.title_text, fill=COLOR_TEXT2, font=FONT_BOLD)
            return
            
        from PIL import Image, ImageEnhance, ImageDraw, ImageTk
        
        if dim != getattr(self, '_cached_dim', 0) or getattr(self, '_cached_normal', None) is None:
            self._cached_dim = dim
            resized = self.original_img.resize((dim, dim), Image.Resampling.LANCZOS)
            self._cached_normal = ImageTk.PhotoImage(resized)
            
            h_img = ImageEnhance.Brightness(resized.copy()).enhance(0.4)
            draw = ImageDraw.Draw(h_img)
            draw.text((dim // 2, dim // 2), f"TROCAR {self.title_text.upper()}", fill=(255, 255, 255), anchor="mm")
            self._cached_hover = ImageTk.PhotoImage(h_img)
            
        img_to_show = self._cached_hover if self.hover_active else self._cached_normal
        self.canvas.create_image(cx, cy, image=img_to_show)
        self.canvas.create_rectangle(cx - dim//2, cy - dim//2, cx + dim//2, cy + dim//2, outline=COLOR_BORDER, width=2)

    def load_cover(self, path):
        self.cover_path = path
        if not path or not os.path.exists(path):
            self.original_img = None
            self._cached_normal = None
            self._cached_hover = None
            self.redraw()
            return
        from PIL import Image
        try:
            self.original_img = Image.open(path).convert("RGB")
            self._cached_dim = 0
            self.redraw()
        except Exception as e:
            print("Error loading cover image:", e)

    def show_image_options(self, event=None):
        if getattr(self, 'camera_active', False):
            return
        if not self.app:
            return

        dlg = customtkinter.CTkToplevel(self)
        dlg.title("Imagem da Capa")
        dlg.geometry("340x200")
        dlg.resizable(False, False)
        dlg.attributes("-topmost", True)
        dlg.configure(fg_color=COLOR_BG)
        
        try:
            x = self.winfo_rootx() + (self.winfo_width() // 2) - 170
            y = self.winfo_rooty() + (self.winfo_height() // 2) - 100
            dlg.geometry(f"+{x}+{y}")
        except:
            pass

        lbl = customtkinter.CTkLabel(dlg, text=f"Alterar {self.title_text}", font=FONT_TITLE, text_color=COLOR_ACCENT)
        lbl.pack(pady=(18, 12))

        def on_cam():
            dlg.destroy()
            self.start_in_place_camera()

        def on_pc():
            dlg.destroy()
            self.load_from_pc()

        btn_cam = customtkinter.CTkButton(
            dlg, text="📸 Tirar Foto com a Câmera", height=36,
            font=FONT_BOLD, fg_color=COLOR_ACCENT, text_color="#000",
            hover_color=COLOR_BORDER, corner_radius=6, command=on_cam
        )
        btn_cam.pack(fill="x", padx=25, pady=4)

        btn_pc = customtkinter.CTkButton(
            dlg, text="📁 Carregar Imagem do PC", height=36,
            font=FONT_BOLD, fg_color=COLOR_SURFACE2, text_color=COLOR_TEXT,
            hover_color=COLOR_BORDER, corner_radius=6, command=on_pc
        )
        btn_pc.pack(fill="x", padx=25, pady=4)

    def load_from_pc(self):
        from tkinter import filedialog
        import shutil
        from PIL import Image
        path = filedialog.askopenfilename(title=f"Escolha a imagem para {self.title_text}", filetypes=[("Imagens", "*.jpg *.jpeg *.png")])
        if path:
            save_path = os.path.join(self.app.project_dir, f"{'cover' if 'Frontal' in self.title_text else 'back'}.jpg")
            try:
                img = Image.open(path).convert("RGB")
                w, h = img.size
                min_dim = min(h, w)
                sx, sy = (w - min_dim) // 2, (h - min_dim) // 2
                img = img.crop((sx, sy, sx+min_dim, sy+min_dim))
                img.save(save_path, quality=92)
            except:
                shutil.copy(path, save_path)
            self.load_cover(save_path)

    def start_in_place_camera(self):
        if getattr(self, 'camera_active', False):
            return
        import cv2
        self.cap = cv2.VideoCapture(0)
        if not self.cap or not self.cap.isOpened():
            messagebox.showwarning("Câmera", "Não foi possível acessar a câmera do computador.")
            if self.cap: self.cap.release()
            self.cap = None
            return

        self.camera_active = True
        self._latest_cam_frame = None

        self.cam_controls = customtkinter.CTkFrame(self, fg_color=COLOR_SURFACE, height=36, corner_radius=6)
        self.cam_controls.place(relx=0.04, rely=0.82, relwidth=0.92, height=34)

        self.btn_capture = customtkinter.CTkButton(
            self.cam_controls, text="📸 CAPTURAR", height=26,
            fg_color=COLOR_ACCENT, text_color="#000", font=FONT_BOLD,
            command=self.capture_photo
        )
        self.btn_capture.pack(side="left", fill="x", expand=True, padx=(4, 2), pady=4)

        self.btn_cancel_cam = customtkinter.CTkButton(
            self.cam_controls, text="❌ CANCELAR", height=26,
            fg_color=COLOR_SURFACE2, text_color=COLOR_TEXT, font=FONT_MAIN,
            command=self.stop_in_place_camera
        )
        self.btn_cancel_cam.pack(side="left", fill="x", expand=True, padx=(2, 4), pady=4)

        self._stream_camera()

    def _stream_camera(self):
        if not getattr(self, 'camera_active', False) or not self.cap:
            return
        import cv2
        from PIL import Image, ImageTk

        ret, frame = self.cap.read()
        if ret:
            # Espelhamento horizontal para movimento natural
            frame = cv2.flip(frame, 1)
            frame = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            h, w, _ = frame.shape
            min_dim = min(h, w)
            sx, sy = (w - min_dim) // 2, (h - min_dim) // 2
            frame_sq = frame[sy:sy+min_dim, sx:sx+min_dim]
            self._latest_cam_frame = Image.fromarray(frame_sq)

            cw = self.canvas.winfo_width()
            ch = self.canvas.winfo_height()
            dim = max(10, min(cw, ch) - 10)
            cx, cy = cw // 2, ch // 2

            preview_img = self._latest_cam_frame.resize((dim, dim), Image.Resampling.BILINEAR)
            self.tk_cam_img = ImageTk.PhotoImage(preview_img)

            self.canvas.delete("all")
            self.canvas.create_image(cx, cy, image=self.tk_cam_img)
            self.canvas.create_rectangle(cx - dim//2, cy - dim//2, cx + dim//2, cy + dim//2, outline=COLOR_ACCENT, width=2)

        self.after(30, self._stream_camera)

    def capture_photo(self):
        if hasattr(self, '_latest_cam_frame') and self._latest_cam_frame:
            save_path = os.path.join(self.app.project_dir, f"{'cover' if 'Frontal' in self.title_text else 'back'}.jpg")
            try:
                self._latest_cam_frame.save(save_path, quality=92)
            except Exception as e:
                print("Error saving captured photo:", e)
            self.stop_in_place_camera()
            self.load_cover(save_path)
        else:
            self.stop_in_place_camera()

    def stop_in_place_camera(self):
        self.camera_active = False
        if hasattr(self, 'cap') and self.cap:
            try:
                self.cap.release()
            except:
                pass
            self.cap = None
        if hasattr(self, 'cam_controls') and self.cam_controls and self.cam_controls.winfo_exists():
            self.cam_controls.destroy()
            self.cam_controls = None
        self._latest_cam_frame = None
        self.redraw()

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
        super().__init__(master, fg_color=COLOR_SURFACE, corner_radius=8, border_width=2, border_color=COLOR_BORDER, **kwargs)
        self.on_album_found = on_album_found
        self.is_collapsed = False
        self.columnconfigure(0, weight=1)
        self.rowconfigure(1, weight=1)
        
        self.header = customtkinter.CTkFrame(self, fg_color="transparent")
        self.header.grid(row=0, column=0, sticky="ew", padx=15, pady=10)
        
        self.lbl_title = customtkinter.CTkLabel(self.header, text="[ ADICIONAR INFORMAÇÕES DO DISCO ]", font=FONT_BOLD, text_color=COLOR_TEXT)
        self.lbl_title.pack(side="left")
        
        # Flecha laranja padrão idêntica aos outros painéis
        self.btn_toggle = customtkinter.CTkButton(
            self.header, text="▼", width=30, height=24, fg_color="transparent",
            text_color=COLOR_ACCENT, hover_color=COLOR_SURFACE2, font=FONT_BOLD,
            corner_radius=4, command=self.toggle
        )
        self.btn_toggle.pack(side="right", padx=(5, 0))
        
        self.btn_search = customtkinter.CTkButton(self.header, text="🔍 Buscar Álbum", width=120, height=24, fg_color=COLOR_SURFACE2, text_color=COLOR_TEXT, font=FONT_MAIN, command=self.search_album)
        self.btn_search.pack(side="right", padx=(0, 5))
        
        self.content_frame = customtkinter.CTkFrame(self, fg_color="transparent")
        self.content_frame.grid(row=1, column=0, sticky="nsew", padx=5, pady=(0, 10))
        
        self.entries = {}
        self.field_labels = []
        fields = [("Artista / Banda", "artist"), ("Álbum", "album"), ("Ano", "year"), ("Agulha/Cápsula", "cartridge"), ("Vinil (ex: 180g)", "vinyl_spec")]
        for label_text, key in fields:
            f = customtkinter.CTkFrame(self.content_frame, fg_color="transparent")
            f.pack(fill="x", padx=15, pady=2)
            lbl = customtkinter.CTkLabel(f, text=label_text, font=FONT_LABEL, text_color=COLOR_TEXT2, width=150, anchor="e")
            lbl.pack(side="left", padx=(0, 10))
            self.field_labels.append(lbl)
            
            if key == "cartridge":
                ent = customtkinter.CTkComboBox(f, values=CARTRIDGES, fg_color=COLOR_BG, border_color=COLOR_BORDER, text_color=COLOR_TEXT, font=FONT_MAIN, dropdown_font=FONT_MAIN, command=lambda v: None)
                ent.set("")
                ent.bind("<KeyRelease>", self._filter_cartridges)
            else:
                ent = customtkinter.CTkEntry(f, fg_color=COLOR_BG, border_color=COLOR_BORDER, text_color=COLOR_TEXT, font=FONT_MAIN)
            ent.pack(side="left", fill="x", expand=True)
            self.entries[key] = ent

    def update_theme(self):
        self.configure(fg_color=COLOR_SURFACE, border_color=COLOR_BORDER)
        self.lbl_title.configure(text_color=COLOR_TEXT)
        self.btn_toggle.configure(text_color=COLOR_ACCENT, hover_color=COLOR_SURFACE2)
        self.btn_search.configure(fg_color=COLOR_SURFACE2, text_color=COLOR_TEXT)
        for ent in self.entries.values():
            ent.configure(fg_color=COLOR_BG, border_color=COLOR_BORDER, text_color=COLOR_TEXT)
        for lbl in self.field_labels:
            lbl.configure(text_color=COLOR_TEXT2)

    def toggle(self):
        if self.is_collapsed:
            self.content_frame.grid(row=1, column=0, sticky="nsew", padx=5, pady=(0, 10))
            self.btn_toggle.configure(text="▼")
            self.is_collapsed = False
        else:
            self.content_frame.grid_remove()
            self.btn_toggle.configure(text="▲")
            self.is_collapsed = True

    def set_collapsed(self, collapse: bool):
        if collapse and not self.is_collapsed:
            self.content_frame.grid_remove()
            self.btn_toggle.configure(text="▲")
            self.is_collapsed = True
        elif not collapse and self.is_collapsed:
            self.content_frame.grid(row=1, column=0, sticky="nsew", padx=5, pady=(0, 10))
            self.btn_toggle.configure(text="▼")
            self.is_collapsed = False

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
        super().__init__(master, fg_color=COLOR_SURFACE, corner_radius=8, border_width=2, border_color=COLOR_BORDER, **kwargs)
        self.is_collapsed = False
        self.columnconfigure(0, weight=1)
        self.rowconfigure(1, weight=1)
        
        self.header = customtkinter.CTkFrame(self, fg_color="transparent")
        self.header.grid(row=0, column=0, sticky="ew", padx=15, pady=10)
        
        self.lbl_title = customtkinter.CTkLabel(self.header, text=title, font=FONT_BOLD, text_color=COLOR_TEXT)
        self.lbl_title.pack(side="left")
        
        # Orange arrow
        self.btn_toggle = customtkinter.CTkButton(self.header, text="▼", width=30, height=24, fg_color="transparent", text_color=COLOR_ACCENT, hover_color=COLOR_SURFACE2, font=FONT_BOLD, corner_radius=4, command=self.toggle)
        self.btn_toggle.pack(side="right")
        
        self.content_frame = customtkinter.CTkFrame(self, fg_color="transparent")
        self.content_frame.grid(row=1, column=0, sticky="nsew", padx=5, pady=(0, 10))
        
    def toggle(self):
        if self.is_collapsed:
            self.content_frame.grid(row=1, column=0, sticky="nsew", padx=5, pady=(0, 10))
            self.btn_toggle.configure(text="▼")
            self.is_collapsed = False
        else:
            self.content_frame.grid_remove()
            self.btn_toggle.configure(text="▲")
            self.is_collapsed = True

    def set_collapsed(self, collapse: bool):
        if collapse and not self.is_collapsed:
            self.content_frame.grid_remove()
            self.btn_toggle.configure(text="▲")
            self.is_collapsed = True
        elif not collapse and self.is_collapsed:
            self.content_frame.grid(row=1, column=0, sticky="nsew", padx=5, pady=(0, 10))
            self.btn_toggle.configure(text="▼")
            self.is_collapsed = False

    def update_theme(self):
        self.configure(fg_color=COLOR_SURFACE, border_color=COLOR_BORDER)
        self.lbl_title.configure(text_color=COLOR_TEXT)
        self.btn_toggle.configure(text_color=COLOR_ACCENT, hover_color=COLOR_SURFACE2)

class FileBrowser(customtkinter.CTkFrame):
    def __init__(self, master, app, **kwargs):
        super().__init__(master, fg_color="transparent", **kwargs)
        self.app = app
        
        # Linha 1: Caminho completo da pasta
        top_path = customtkinter.CTkFrame(self, fg_color="transparent")
        top_path.pack(fill="x", pady=(0, 4))
        
        self.lbl_path = customtkinter.CTkLabel(
            top_path, text="...", font=(FONT_FAMILY, 10, "bold"),
            text_color=COLOR_TEXT, anchor="w"
        )
        self.lbl_path.pack(side="left", fill="x", expand=True)
        
        # Linha 2: Botões de ação rápida
        top_btns = customtkinter.CTkFrame(self, fg_color="transparent")
        top_btns.pack(fill="x", pady=(0, 4))
        
        self.btn_up = customtkinter.CTkButton(
            top_btns, text="⬆️ Subir", width=65, height=24,
            fg_color=COLOR_SURFACE2, text_color=COLOR_TEXT, font=FONT_MAIN,
            command=self.go_up
        )
        self.btn_up.pack(side="left", padx=(0, 4))
        
        self.btn_change = customtkinter.CTkButton(
            top_btns, text="📁 Alterar", width=75, height=24,
            fg_color=COLOR_SURFACE2, text_color=COLOR_TEXT, font=FONT_MAIN,
            command=self.app._choose_folder
        )
        self.btn_change.pack(side="left", padx=2)
        
        self.btn_open = customtkinter.CTkButton(
            top_btns, text="📂 Abrir no PC", width=95, height=24,
            fg_color=COLOR_SURFACE2, text_color=COLOR_TEXT, font=FONT_MAIN,
            command=self.open_explorer
        )
        self.btn_open.pack(side="left", padx=2)
        
        self.btn_new = customtkinter.CTkButton(
            top_btns, text="➕ Nova Pasta", width=95, height=24,
            fg_color=COLOR_SURFACE2, text_color=COLOR_TEXT, font=FONT_MAIN,
            command=self.new_folder
        )
        self.btn_new.pack(side="right")
        
        # Lista com scroll fixo compacto (102px) para casar perfeitamente com a altura do Gravador (226px)
        self.scroll = customtkinter.CTkScrollableFrame(
            self, fg_color=COLOR_BG, border_width=1, border_color=COLOR_SURFACE2, height=102
        )
        self.scroll.pack(fill="both", expand=True, pady=(2, 0))
        self.scroll._parent_frame.grid_propagate(False)
        self.scroll._parent_frame.configure(height=102)

    def open_explorer(self):
        try:
            os.startfile(self.app.project_dir)
        except Exception as e:
            print("Open explorer error:", e)

    def go_up(self):
        parent = os.path.dirname(self.app.project_dir)
        if parent and os.path.isdir(parent) and parent != self.app.project_dir:
            self.app.set_project_dir(parent)

    def update_browser(self):
        self.lbl_path.configure(text=f"📁 {self.app.project_dir}")
        self.scroll._parent_frame.grid_propagate(False)
        self.scroll._parent_frame.configure(height=102)
        for w in self.scroll.winfo_children():
            w.destroy()
            
        cur = self.app.project_dir
        if not os.path.isdir(cur):
            return
            
        try:
            entries = os.listdir(cur)
            dirs = []
            audio_files = []
            img_files = []
            other_files = []
            
            for item in sorted(entries, key=lambda s: s.lower()):
                full = os.path.join(cur, item)
                if os.path.isdir(full):
                    dirs.append((item, full))
                else:
                    ext = os.path.splitext(item)[1].lower()
                    if ext in ('.wav', '.mp3', '.flac', '.ogg'):
                        audio_files.append((item, full))
                    elif ext in ('.jpg', '.jpeg', '.png'):
                        img_files.append((item, full))
                    else:
                        other_files.append((item, full))
                        
            if not dirs and not audio_files and not img_files and not other_files:
                lbl = customtkinter.CTkLabel(self.scroll, text="(Pasta vazia)", font=FONT_MAIN, text_color=COLOR_TEXT2)
                lbl.pack(pady=10)
                return

            # Pastas
            for item, full in dirs:
                f_row = customtkinter.CTkFrame(self.scroll, fg_color="transparent")
                f_row.pack(fill="x", pady=1)
                btn = customtkinter.CTkButton(
                    f_row, text=f"📁 {item}", fg_color="transparent",
                    text_color=COLOR_TEXT, hover_color=COLOR_SURFACE2,
                    anchor="w", font=FONT_MAIN, height=22,
                    command=lambda p=full: self.app.set_project_dir(p)
                )
                btn.pack(side="left", fill="x", expand=True)

            # Arquivos de áudio
            for item, full in audio_files:
                f_row = customtkinter.CTkFrame(self.scroll, fg_color="transparent")
                f_row.pack(fill="x", pady=1)
                btn = customtkinter.CTkButton(
                    f_row, text=f"🎵 {item}", fg_color="transparent",
                    text_color=COLOR_ACCENT, hover_color=COLOR_SURFACE2,
                    anchor="w", font=FONT_MAIN, height=22,
                    command=lambda p=full: os.startfile(p)
                )
                btn.pack(side="left", fill="x", expand=True)

            # Imagens
            for item, full in img_files:
                f_row = customtkinter.CTkFrame(self.scroll, fg_color="transparent")
                f_row.pack(fill="x", pady=1)
                btn = customtkinter.CTkButton(
                    f_row, text=f"🖼️ {item}", fg_color="transparent",
                    text_color=COLOR_GREEN, hover_color=COLOR_SURFACE2,
                    anchor="w", font=FONT_MAIN, height=22,
                    command=lambda p=full: os.startfile(p)
                )
                btn.pack(side="left", fill="x", expand=True)

            # Outros
            for item, full in other_files:
                f_row = customtkinter.CTkFrame(self.scroll, fg_color="transparent")
                f_row.pack(fill="x", pady=1)
                btn = customtkinter.CTkButton(
                    f_row, text=f"📄 {item}", fg_color="transparent",
                    text_color=COLOR_TEXT2, hover_color=COLOR_SURFACE2,
                    anchor="w", font=FONT_MAIN, height=22,
                    command=lambda p=full: os.startfile(p)
                )
                btn.pack(side="left", fill="x", expand=True)

        except Exception as e:
            print("Update browser error:", e)

    def new_folder(self):
        import tkinter.simpledialog
        name = tkinter.simpledialog.askstring("Nova Pasta", "Nome da nova subpasta:")
        if name and name.strip():
            new_path = os.path.join(self.app.project_dir, name.strip())
            os.makedirs(new_path, exist_ok=True)
            self.app.set_project_dir(new_path)

    def update_theme(self):
        self.lbl_path.configure(text_color=COLOR_TEXT)
        self.btn_up.configure(fg_color=COLOR_SURFACE2, text_color=COLOR_TEXT)
        self.btn_change.configure(fg_color=COLOR_SURFACE2, text_color=COLOR_TEXT)
        self.btn_open.configure(fg_color=COLOR_SURFACE2, text_color=COLOR_TEXT)
        self.btn_new.configure(fg_color=COLOR_SURFACE2, text_color=COLOR_TEXT)
        self.scroll.configure(fg_color=COLOR_BG, border_color=COLOR_SURFACE2)
        self.update_browser()


class VUPanel(customtkinter.CTkFrame):
    def __init__(self, master, title, knob_label, command, init_val, max_val, **kwargs):
        super().__init__(master, fg_color=COLOR_SURFACE, corner_radius=8, border_width=2, border_color=COLOR_BORDER, height=140, **kwargs)
        self.pack_propagate(False)
        self.vu = AnalogVUMeter(self, label=title)
        self.vu.pack(side="left", padx=(10, 5), pady=10)
        self.knob = RotaryKnob(self, label=knob_label, command=command, init_val=init_val, max_val=max_val)
        self.knob.pack(side="left", padx=(5, 10), pady=10)

    def update_theme(self):
        self.configure(fg_color=COLOR_SURFACE, border_color=COLOR_BORDER)
        self.vu.update_theme()
        self.knob.update_theme()

class EqualizerPanel(customtkinter.CTkFrame):
    def __init__(self, master, on_bass, on_mid, on_treble, **kwargs):
        # Altura 140 idêntica aos VUPanels das laterais para simetria perfeita
        super().__init__(master, fg_color=COLOR_SURFACE, corner_radius=8, border_width=2, border_color=COLOR_BORDER, height=140, **kwargs)
        self.pack_propagate(False)
        
        self.lbl_title = customtkinter.CTkLabel(self, text="EQUALIZADOR", font=(FONT_FAMILY, 10, "bold"), text_color=COLOR_ACCENT)
        self.lbl_title.pack(pady=(8, 4))
        
        knobs_row = customtkinter.CTkFrame(self, fg_color="transparent")
        knobs_row.pack(expand=True, padx=10, pady=(0, 8))
        
        self.knob_bass = RotaryKnob(knobs_row, label="GRAVE", command=on_bass, init_val=1.0, max_val=2.5)
        self.knob_bass.pack(side="left", padx=6)
        
        self.knob_mid = RotaryKnob(knobs_row, label="MÉDIO", command=on_mid, init_val=1.0, max_val=2.5)
        self.knob_mid.pack(side="left", padx=6)
        
        self.knob_treble = RotaryKnob(knobs_row, label="AGUDO", command=on_treble, init_val=1.0, max_val=2.5)
        self.knob_treble.pack(side="left", padx=6)

    def update_theme(self):
        self.configure(fg_color=COLOR_SURFACE, border_color=COLOR_BORDER)
        self.lbl_title.configure(text_color=COLOR_ACCENT)
        self.knob_bass.update_theme()
        self.knob_mid.update_theme()
        self.knob_treble.update_theme()

class App(customtkinter.CTk):
    def __init__(self):
        super().__init__()
        self.current_theme = CURRENT_THEME
        self.engine = AudioEngine()
        self.engine.on_time_update = self.on_time_update
        self.project_dir = os.path.join(os.path.expanduser("~"), "Music", "OpenVynilRipper", "MeuDisco")
        os.makedirs(self.project_dir, exist_ok=True)
        self.session_recorded = {"LadoA": False, "LadoB": False}
        self.current_recording_side = None
        
        self.title("OPEN VYNIL RIPPER - ANALOG EDITION")
        self.geometry("950x950")
        self.resizable(True, True) # Permite Tela Cheia
        self.configure(fg_color=COLOR_BG)
        self.protocol("WM_DELETE_WINDOW", self.on_closing)
        
        self._build_ui()
        self._init_devices()
        self._ui_update_loop()
        

    def _build_ui(self):
        # 1. Barra de status na base absoluta da janela
        self.status_bar = customtkinter.CTkFrame(self, fg_color=COLOR_SURFACE, height=28, corner_radius=0)
        self.status_bar.pack(fill="x", side="bottom")
        self.lbl_status = customtkinter.CTkLabel(self.status_bar, text="SISTEMA PRONTO", font=FONT_MAIN, text_color=COLOR_GREEN)
        self.lbl_status.pack(side="left", padx=15, pady=3)

        # 2. Linha inferior ancorada acima da barra de status (Gravador e Meu Disco)
        bottom_row = customtkinter.CTkFrame(self, fg_color="transparent")
        bottom_row.pack(fill="x", side="bottom", padx=10, pady=(2, 6))
        bottom_row.columnconfigure(0, weight=1, uniform="c")
        bottom_row.columnconfigure(1, weight=1, uniform="c")
        
        self.rec_col = CollapsibleFrame(bottom_row, title="[ G R A V A D O R ]")
        self.rec_col.grid(row=0, column=0, sticky="nsew", padx=(0, 5))
        trans_frame = self.rec_col.content_frame
        
        self.browser_col = CollapsibleFrame(bottom_row, title="[ M E U   D I S C O ]")
        self.browser_col.grid(row=0, column=1, sticky="nsew", padx=(5, 0))
        self.browser = FileBrowser(self.browser_col.content_frame, app=self)
        self.browser.pack(fill="x", expand=False, padx=10, pady=(2, 2))
        self.browser.update_browser()
        
        # Recorder UI
        # 1. Seção Superior: Opções de Gravação
        f_rec_top = customtkinter.CTkFrame(trans_frame, fg_color="transparent")
        f_rec_top.pack(fill="x", padx=10, pady=(2, 2))
        
        self.chk_magic = customtkinter.CTkSwitch(
            f_rec_top, text="✨ Magic Record (Início Auto)", 
            fg_color="#8a2be2", text_color=COLOR_TEXT, font=FONT_BOLD
        )
        self.chk_magic.pack(side="left", padx=(0, 15))
        
        self.chk_clipping = customtkinter.CTkSwitch(
            f_rec_top, text="Remover Clippings", 
            fg_color=COLOR_ACCENT, text_color=COLOR_TEXT, font=FONT_MAIN
        )
        self.chk_clipping.pack(side="left")
        
        f_rec_fmt = customtkinter.CTkFrame(f_rec_top, fg_color="transparent")
        f_rec_fmt.pack(side="right")
        self.lbl_format = customtkinter.CTkLabel(f_rec_fmt, text="Salvar original em:", font=FONT_BOLD, text_color=COLOR_TEXT2)
        self.lbl_format.pack(side="left", padx=(0, 6))
        self.opt_format = customtkinter.CTkOptionMenu(
            f_rec_fmt, values=["WAV", "FLAC", "MP3", "OGG"], 
            fg_color=COLOR_BG, button_color=COLOR_SURFACE2, font=FONT_MAIN, dropdown_font=FONT_MAIN, width=80, height=24
        )
        self.opt_format.pack(side="right")

        # 2. Seção Central: Ações de Gravação e Timer (Timer alinhado acima de pause/stop)
        f_rec_action = customtkinter.CTkFrame(trans_frame, fg_color="transparent")
        f_rec_action.pack(fill="x", padx=10, pady=(2, 2))
        
        f_timer_row = customtkinter.CTkFrame(f_rec_action, fg_color="transparent")
        f_timer_row.pack(fill="x", pady=(0, 2))
        
        self.lbl_rec_title = customtkinter.CTkLabel(
            f_timer_row, text="● ESTÚDIO DE GRAVAÇÃO", font=(FONT_FAMILY, 10, "bold"), text_color=COLOR_TEXT2
        )
        self.lbl_rec_title.pack(side="left")
        
        self.lbl_timer = customtkinter.CTkLabel(
            f_timer_row, text="00:00.00", font=(FONT_FAMILY, 18, "bold"), text_color=COLOR_RED
        )
        self.lbl_timer.pack(side="right", padx=(0, 6))
        
        f_btns_row = customtkinter.CTkFrame(f_rec_action, fg_color="transparent")
        f_btns_row.pack(fill="x")
        f_btns_row.columnconfigure(0, weight=1, uniform="rb")
        f_btns_row.columnconfigure(1, weight=1, uniform="rb")
        f_btns_row.columnconfigure(2, weight=1, uniform="rb")
        f_btns_row.columnconfigure(3, weight=1, uniform="rb")
        
        self.btn_rec_a = customtkinter.CTkButton(
            f_btns_row, text="● GRAVAR LADO A", fg_color=COLOR_RED, text_color="#FFF",
            font=FONT_BOLD, height=32, corner_radius=6, command=lambda: self.on_rec_side("LadoA")
        )
        self.btn_rec_a.grid(row=0, column=0, sticky="ew", padx=(0, 3))
        
        self.btn_rec_b = customtkinter.CTkButton(
            f_btns_row, text="● GRAVAR LADO B", fg_color=COLOR_RED, text_color="#FFF",
            font=FONT_BOLD, height=32, corner_radius=6, command=lambda: self.on_rec_side("LadoB")
        )
        self.btn_rec_b.grid(row=0, column=1, sticky="ew", padx=3)
        
        self.btn_pause = customtkinter.CTkButton(
            f_btns_row, text="⏸ PAUSE", fg_color=COLOR_SURFACE2, text_color=COLOR_TEXT,
            font=FONT_BOLD, height=32, corner_radius=6, state="disabled", command=self.on_pause_click
        )
        self.btn_pause.grid(row=0, column=2, sticky="ew", padx=3)
        
        self.btn_stop = customtkinter.CTkButton(
            f_btns_row, text="■ STOP", fg_color=COLOR_SURFACE2, text_color=COLOR_TEXT,
            font=FONT_BOLD, height=32, corner_radius=6, state="disabled", command=self.on_stop_click
        )
        self.btn_stop.grid(row=0, column=3, sticky="ew", padx=(3, 0))

        # 3. Seção Inferior: Opções de Exportação e Botões de Exportação
        f_exp_opts = customtkinter.CTkFrame(trans_frame, fg_color="transparent")
        f_exp_opts.pack(fill="x", padx=10, pady=(4, 2))
        
        self.chk_normalize = customtkinter.CTkSwitch(
            f_exp_opts, text="Normalizar Volume", fg_color=COLOR_ACCENT, text_color=COLOR_TEXT, font=FONT_MAIN
        )
        self.chk_normalize.pack(side="left", padx=(0, 15))
        
        self.chk_denoise = customtkinter.CTkSwitch(
            f_exp_opts, text="Filtro Anti-Chiado", fg_color=COLOR_ACCENT, text_color=COLOR_TEXT, font=FONT_MAIN
        )
        self.chk_denoise.pack(side="left")
        
        f_exp_fmt = customtkinter.CTkFrame(f_exp_opts, fg_color="transparent")
        f_exp_fmt.pack(side="right")
        self.lbl_export = customtkinter.CTkLabel(f_exp_fmt, text="Formato exportação:", font=FONT_BOLD, text_color=COLOR_TEXT2)
        self.lbl_export.pack(side="left", padx=(0, 6))
        self.opt_export = customtkinter.CTkOptionMenu(
            f_exp_fmt, values=["MP3", "FLAC", "WAV", "OGG"], fg_color=COLOR_BG, button_color=COLOR_SURFACE2, font=FONT_MAIN, dropdown_font=FONT_MAIN, width=80, height=24
        )
        self.opt_export.pack(side="right")
        
        f_export_btns = customtkinter.CTkFrame(trans_frame, fg_color="transparent")
        f_export_btns.pack(fill="x", padx=10, pady=(2, 2))
        
        self.btn_export = customtkinter.CTkButton(
            f_export_btns, text="💾 EXPORTAR", fg_color=COLOR_ACCENT, text_color="#000",
            font=FONT_BOLD, height=32, corner_radius=6, command=self.on_export_direct
        )
        self.btn_export.pack(side="left", fill="x", expand=True, padx=(0, 3))
        
        self.btn_split = customtkinter.CTkButton(
            f_export_btns, text="✂ SEPARAR FAIXAS + EXPORTAR", fg_color=COLOR_ACCENT, text_color="#000",
            font=FONT_BOLD, height=32, corner_radius=6, command=self.on_auto_split
        )
        self.btn_split.pack(side="left", fill="x", expand=True, padx=(3, 0))

        # 3. Painel de medição VU e Equalizador ancorado acima do Gravador
        vis_frame = customtkinter.CTkFrame(self, fg_color="transparent")
        vis_frame.pack(fill="x", side="bottom", padx=10, pady=(5, 5))
        
        self.panel_in = VUPanel(vis_frame, title="INPUT VU", knob_label="GAIN", command=self._on_input_gain, init_val=1.0, max_val=3.0)
        self.panel_in.pack(side="left", padx=(0, 5))
        self.vu_in = self.panel_in.vu
        
        self.waveform = RetroWaveform(vis_frame)
        self.waveform.pack(side="left", expand=True, fill="both", padx=5)
        
        self.eq_panel = EqualizerPanel(vis_frame, on_bass=self._on_eq_bass, on_mid=self._on_eq_mid, on_treble=self._on_eq_treble)
        self.eq_panel.pack(side="left", padx=5)
        
        self.panel_out = VUPanel(vis_frame, title="MONITOR VU", knob_label="GAIN", command=self._on_monitor_gain, init_val=1.0, max_val=2.0)
        self.panel_out.pack(side="right", padx=(5, 0))
        self.vu_out = self.panel_out.vu

        # 4. Header no topo absoluto
        self.header = customtkinter.CTkFrame(self, fg_color=COLOR_SURFACE, height=54, corner_radius=0)
        self.header.pack(fill="x", side="top", pady=(0, 10))
        
        self.lbl_header_title = customtkinter.CTkLabel(self.header, text="O P E N   V Y N I L   R I P P E R", font=FONT_TITLE, text_color=COLOR_ACCENT)
        self.lbl_header_title.pack(side="left", padx=20, pady=10)
        
        self.btn_theme = customtkinter.CTkButton(
            self.header, text="☀️ TEMA DIA", width=120, height=32, corner_radius=6,
            fg_color=COLOR_SURFACE2, text_color=COLOR_TEXT, font=FONT_BOLD,
            hover_color=COLOR_BORDER,
            command=self.toggle_theme
        )
        self.btn_theme.pack(side="right", padx=15, pady=8)

        self.btn_toggle_all = customtkinter.CTkButton(
            self.header, text="⇕ RECOLHER TODOS", width=160, height=32, corner_radius=6,
            fg_color=COLOR_SURFACE2, text_color=COLOR_TEXT, font=FONT_BOLD,
            hover_color=COLOR_BORDER,
            command=self.toggle_all_panels
        )
        self.btn_toggle_all.pack(side="right", padx=(0, 5), pady=8)

        # 5. Container central flexível ocupando todo o espaço entre o topo e o painel inferior
        main_container = customtkinter.CTkFrame(self, fg_color="transparent")
        main_container.pack(fill="both", expand=True, side="top", padx=10, pady=5)
        
        top_row = customtkinter.CTkFrame(main_container, fg_color="transparent")
        top_row.pack(fill="x", side="top")
        top_row.columnconfigure(0, weight=1, uniform="a")
        top_row.columnconfigure(1, weight=1, uniform="a")
        
        self.dev_frame = CollapsibleFrame(top_row, title="[ CONEXÕES DE ÁUDIO ]")
        self.dev_frame.grid(row=0, column=0, sticky="nsew", padx=(0, 5))
        dev_content = self.dev_frame.content_frame
        
        f_in = customtkinter.CTkFrame(dev_content, fg_color="transparent")
        f_in.pack(fill="x", padx=15, pady=(6, 2))
        self.lbl_in = customtkinter.CTkLabel(f_in, text="Entrada de Gravação (Vinil):", font=FONT_LABEL, text_color=COLOR_TEXT2)
        self.lbl_in.pack(side="left")
        
        self.btn_mute = customtkinter.CTkButton(
            f_in, text="🔊 Silenciar Entrada", width=140, height=24, corner_radius=4,
            fg_color=COLOR_SURFACE2, text_color=COLOR_TEXT, font=FONT_MAIN,
            hover_color=COLOR_BORDER, command=self.toggle_mute
        )
        self.btn_mute.pack(side="right")
        
        self.opt_in = customtkinter.CTkOptionMenu(dev_content, values=["Nenhum"], font=FONT_MAIN, dropdown_font=FONT_MAIN, fg_color=COLOR_BG, button_color=COLOR_SURFACE2, text_color=COLOR_TEXT, command=self._on_device_change)
        self.opt_in.pack(fill="x", padx=15, pady=(0, 8))
        
        f_out = customtkinter.CTkFrame(dev_content, fg_color="transparent")
        f_out.pack(fill="x", padx=15, pady=(4, 2))
        self.lbl_out = customtkinter.CTkLabel(f_out, text="Saída de Monitoramento (Fone/Caixas):", font=FONT_LABEL, text_color=COLOR_TEXT2)
        self.lbl_out.pack(side="left")
        
        self.opt_out = customtkinter.CTkOptionMenu(dev_content, values=["Nenhum"], font=FONT_MAIN, dropdown_font=FONT_MAIN, fg_color=COLOR_BG, button_color=COLOR_SURFACE2, text_color=COLOR_TEXT, command=self._on_device_change)
        self.opt_out.pack(fill="x", padx=15, pady=(0, 8))
        
        f_win = customtkinter.CTkFrame(dev_content, fg_color="transparent")
        f_win.pack(fill="x", padx=15, pady=(4, 8))
        
        self.lbl_win_default = customtkinter.CTkLabel(f_win, text="🖥️ Saída Padrão do Windows: Carregando...", font=FONT_LABEL, text_color=COLOR_TEXT)
        self.lbl_win_default.pack(side="left", fill="x", expand=True)
        
        self.btn_snd_settings = customtkinter.CTkButton(
            f_win, text="⚙️ Som padrão do Windows", width=190, height=28, corner_radius=6,
            fg_color=COLOR_SURFACE2, text_color=COLOR_TEXT, font=FONT_LABEL,
            command=self._open_windows_sound_settings
        )
        self.btn_snd_settings.pack(side="right", padx=(10, 0))
        
        self.meta_card = MetadataCard(top_row, on_album_found=self._fetch_cover_art)
        self.meta_card.grid(row=0, column=1, sticky="nsew", padx=(5, 0))
        
        mid_row = customtkinter.CTkFrame(main_container, fg_color="transparent")
        mid_row.pack(fill="both", expand=True, side="top", pady=(10,0))
        mid_row.columnconfigure(0, weight=1, uniform="b")
        mid_row.columnconfigure(1, weight=1, uniform="b")
        mid_row.columnconfigure(2, weight=1, uniform="b")
        mid_row.rowconfigure(0, weight=1)
        
        self.turntable = VirtualTurntable(mid_row, app=self)
        self.turntable.grid(row=0, column=0, sticky="nsew", padx=2, pady=2)
        
        pf_front = os.path.join(os.path.expanduser("~"), "Music", "OpenVynilRipper", "assets", "default_front.jpg")
        pf_back = os.path.join(os.path.expanduser("~"), "Music", "OpenVynilRipper", "assets", "default_back.jpg")
        
        self.cover_front = CoverDisplay(mid_row, app=self, title="Capa Frontal")
        self.cover_front.grid(row=0, column=1, sticky="nsew", padx=2, pady=2)
        self.cover_front.load_cover(pf_front)
        
        self.cover_back = CoverDisplay(mid_row, app=self, title="Contracapa")
        self.cover_back.grid(row=0, column=2, sticky="nsew", padx=2, pady=2)
        self.cover_back.load_cover(pf_back)
        
        self.update_buttons_state()

    def toggle_all_panels(self):
        panels = [self.dev_frame, self.meta_card, self.rec_col, self.browser_col]
        any_open = any(not p.is_collapsed for p in panels)
        if any_open:
            for p in panels:
                p.set_collapsed(True)
            self.btn_toggle_all.configure(text="⇕ EXPANDIR TODOS")
        else:
            for p in panels:
                p.set_collapsed(False)
            self.btn_toggle_all.configure(text="⇕ RECOLHER TODOS")
        self.update_idletasks()
        self.turntable.draw_vinyl()
        self.cover_front.redraw()
        self.cover_back.redraw()

    def toggle_theme(self):
        global COLOR_BG, COLOR_SURFACE, COLOR_SURFACE2, COLOR_BORDER, COLOR_ACCENT
        global COLOR_TEXT, COLOR_TEXT2, COLOR_TEXT3, COLOR_RED, COLOR_YELLOW, COLOR_GREEN, CURRENT_THEME
        
        self.current_theme = "light" if self.current_theme == "dark" else "dark"
        CURRENT_THEME = self.current_theme
        t = THEMES[self.current_theme]
        
        COLOR_BG       = t["bg"]
        COLOR_SURFACE  = t["surface"]
        COLOR_SURFACE2 = t["surface2"]
        COLOR_BORDER   = t["border"]
        COLOR_ACCENT   = t["accent"]
        COLOR_TEXT     = t["text"]
        COLOR_TEXT2    = t["text2"]
        COLOR_TEXT3    = t["text3"]
        COLOR_RED      = t["red"]
        COLOR_YELLOW   = t["yellow"]
        COLOR_GREEN    = t["green"]
        
        customtkinter.set_appearance_mode(t["ctk_mode"])
        
        if self.current_theme == "light":
            self.btn_theme.configure(text="🌙 TEMA NOITE")
        else:
            self.btn_theme.configure(text="☀️ TEMA DIA")
            
        self.configure(fg_color=COLOR_BG)
        self._apply_theme_colors()

    def _apply_theme_colors(self):
        self.header.configure(fg_color=COLOR_SURFACE)
        self.lbl_header_title.configure(text_color=COLOR_ACCENT)
        self.btn_theme.configure(fg_color=COLOR_SURFACE2, text_color=COLOR_TEXT, hover_color=COLOR_BORDER)
        self.btn_toggle_all.configure(fg_color=COLOR_SURFACE2, text_color=COLOR_TEXT, hover_color=COLOR_BORDER)
        
        self.dev_frame.update_theme()
        self.lbl_in.configure(text_color=COLOR_TEXT2)
        self.lbl_out.configure(text_color=COLOR_TEXT2)
        self.opt_in.configure(fg_color=COLOR_BG, button_color=COLOR_SURFACE2, text_color=COLOR_TEXT)
        self.opt_out.configure(fg_color=COLOR_BG, button_color=COLOR_SURFACE2, text_color=COLOR_TEXT)
        self.lbl_win_default.configure(text_color=COLOR_TEXT)
        self.btn_snd_settings.configure(fg_color=COLOR_SURFACE2, text_color=COLOR_TEXT)
        
        self.meta_card.update_theme()
        
        self.turntable.update_theme()
        self.cover_front.update_theme()
        self.cover_back.update_theme()
        
        self.panel_in.update_theme()
        self.waveform.update_theme()
        self.eq_panel.update_theme()
        self.panel_out.update_theme()
        
        self.rec_col.update_theme()
        self.chk_magic.configure(text_color=COLOR_TEXT)
        if self.btn_pause.cget("state") == "disabled":
            self.btn_pause.configure(fg_color=COLOR_SURFACE2, text_color=COLOR_TEXT)
        if self.btn_stop.cget("state") == "disabled":
            self.btn_stop.configure(fg_color=COLOR_SURFACE2, text_color=COLOR_TEXT)
        self.chk_normalize.configure(text_color=COLOR_TEXT, fg_color=COLOR_ACCENT)
        self.chk_denoise.configure(text_color=COLOR_TEXT, fg_color=COLOR_ACCENT)
        self.chk_clipping.configure(text_color=COLOR_TEXT, fg_color=COLOR_ACCENT)
        self.lbl_format.configure(text_color=COLOR_TEXT2)
        self.lbl_export.configure(text_color=COLOR_TEXT2)
        self.opt_format.configure(fg_color=COLOR_BG, button_color=COLOR_SURFACE2, text_color=COLOR_TEXT)
        self.opt_export.configure(fg_color=COLOR_BG, button_color=COLOR_SURFACE2, text_color=COLOR_TEXT)
        self.lbl_rec_title.configure(text_color=COLOR_TEXT2)
        self.btn_export.configure(fg_color=COLOR_ACCENT)
        self.btn_split.configure(fg_color=COLOR_ACCENT)
        
        if getattr(self.engine, 'muted', False):
            self.btn_mute.configure(fg_color=COLOR_RED, text_color="#FFF")
        else:
            self.btn_mute.configure(fg_color=COLOR_SURFACE2, text_color=COLOR_TEXT)
        
        self.browser_col.update_theme()
        self.browser.update_theme()
        
        self.status_bar.configure(fg_color=COLOR_SURFACE)
        self.update_buttons_state()

    def toggle_mute(self):
        self.engine.muted = not self.engine.muted
        if self.engine.muted:
            self.btn_mute.configure(text="🔇 Entrada Mutada", fg_color=COLOR_RED, text_color="#FFF")
            self.lbl_status.configure(text="ENTRADA DE ÁUDIO MUTADA (SILENCIADA)", text_color=COLOR_RED)
        else:
            self.btn_mute.configure(text="🔊 Silenciar Entrada", fg_color=COLOR_SURFACE2, text_color=COLOR_TEXT)
            self.lbl_status.configure(text="ENTRADA DE ÁUDIO ATIVA", text_color=COLOR_GREEN)

    def _on_input_gain(self, val): self.engine.input_gain = float(val)
    def _on_monitor_gain(self, val): self.engine.monitor_volume = float(val)
    def _on_eq_bass(self, val): self.engine.eq_bass = float(val)
    def _on_eq_mid(self, val): self.engine.eq_mid = float(val)
    def _on_eq_treble(self, val): self.engine.eq_treble = float(val)
    
    def update_buttons_state(self):
        lado_a_recorded = getattr(self, 'session_recorded', {}).get("LadoA", False)
        lado_b_recorded = getattr(self, 'session_recorded', {}).get("LadoB", False)
        
        self.btn_rec_a.configure(
            text="● REGRAVAR LADO A" if lado_a_recorded else "● GRAVAR LADO A",
            fg_color=COLOR_RED, text_color="#FFF"
        )
        self.btn_rec_b.configure(
            text="● REGRAVAR LADO B" if lado_b_recorded else "● GRAVAR LADO B",
            fg_color=COLOR_RED, text_color="#FFF"
        )

    def _init_devices(self):
        devs = self.engine.get_devices()
        self.in_map = {d["name"]: d["index"] for d in devs["inputs"]}
        self.out_map = {d["name"]: d["index"] for d in devs["outputs"]}
        if self.in_map:
            self.opt_in.configure(values=list(self.in_map.keys()))
            self.opt_in.set(next((n for n in self.in_map.keys() if "ION" in n.upper() or "USB" in n.upper()), list(self.in_map.keys())[0]))
        if self.out_map:
            self.opt_out.configure(values=list(self.out_map.keys()))
            preferred_out = next((n for n in self.out_map.keys() if "SYNAPTICS" in n.upper() or "REALTEK" in n.upper() or "PADRÃO" in n.upper()), list(self.out_map.keys())[0])
            self.opt_out.set(preferred_out)
            
        # Detecta e mostra o dispositivo de saída padrão atual do Windows
        try:
            def_out_idx = sd.default.device[1]
            all_devs = sd.query_devices()
            def_name = all_devs[def_out_idx]['name'] if 0 <= def_out_idx < len(all_devs) else "Desconhecido"
            self.lbl_win_default.configure(text=f"🖥️ Saída Padrão do Windows: {def_name}")
        except:
            self.lbl_win_default.configure(text="🖥️ Saída Padrão do Windows: Áudio Padrão do Sistema")

        self._start_stream()

    def _open_windows_sound_settings(self):
        try:
            # Abre o painel moderno de som do Windows
            subprocess.Popen("start ms-settings:sound", shell=True)
        except:
            try:
                subprocess.Popen("control mmsys.cpl", shell=True)
            except Exception as e:
                print("Error opening sound settings:", e)

    def set_project_dir(self, new_dir):
        self.project_dir = new_dir
        self.session_recorded = {"LadoA": False, "LadoB": False}
        self.current_recording_side = None
        self.browser.update_browser()
        self.update_buttons_state()
        cov_f = os.path.join(self.project_dir, "cover.jpg")
        cov_b = os.path.join(self.project_dir, "back.jpg")
        lbl_v = os.path.join(self.project_dir, "label.jpg")
        self.cover_front.load_cover(cov_f if os.path.exists(cov_f) else None)
        self.cover_back.load_cover(cov_b if os.path.exists(cov_b) else None)
        self.turntable.label_path = lbl_v if os.path.exists(lbl_v) else None
        self.turntable.draw_vinyl()

    def _choose_folder(self):
        folder = filedialog.askdirectory(initialdir=self.project_dir, title="Escolha a pasta do disco")
        if folder:
            self.set_project_dir(folder)

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
                
        self.current_recording_side = side_name
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
        self.btn_export.configure(state="disabled")
        self.btn_split.configure(state="disabled")
        self.btn_pause.configure(state="normal", fg_color=COLOR_ACCENT, text="⏸ PAUSE")
        self.btn_stop.configure(state="normal", fg_color=COLOR_ACCENT)
        self.lbl_status.configure(text=f"GRAVANDO {side_name.upper()}...", text_color=COLOR_RED)
        
    def on_pause_click(self):
        if self.engine.paused:
            self.engine.resume()
            self.btn_pause.configure(text="⏸ PAUSE", fg_color=COLOR_ACCENT)
            self.lbl_status.configure(text="GRAVANDO...", text_color=COLOR_RED)
            self.turntable.set_playing(True)
        else:
            self.engine.pause()
            self.btn_pause.configure(text="▶ RESUME", fg_color=COLOR_YELLOW)
            self.lbl_status.configure(text="GRAVAÇÃO PAUSADA.", text_color=COLOR_YELLOW)
            self.turntable.set_playing(False)

    def on_stop_click(self):
        self.engine.stop_recording()
        self.engine.magic_wait = False
        self.turntable.set_playing(False)
        if getattr(self, 'current_recording_side', None):
            self.session_recorded[self.current_recording_side] = True
            self.current_recording_side = None
        self.btn_rec_a.configure(state="normal")
        self.btn_rec_b.configure(state="normal")
        self.btn_export.configure(state="normal")
        self.btn_split.configure(state="normal")
        self.btn_pause.configure(state="disabled", fg_color=COLOR_SURFACE2, text="⏸ PAUSE")
        self.btn_stop.configure(state="disabled", fg_color=COLOR_SURFACE2)
        self.lbl_status.configure(text="GRAVAÇÃO FINALIZADA. PRONTO.", text_color=COLOR_GREEN)
        self.update_buttons_state()
        self.browser.update_browser()

    def on_export_direct(self):
        lado_a = os.path.join(self.project_dir, "LadoA.wav")
        lado_b = os.path.join(self.project_dir, "LadoB.wav")
        files = []
        if os.path.exists(lado_a): files.append(("Lado A", lado_a))
        if os.path.exists(lado_b): files.append(("Lado B", lado_b))
        if not files:
            messagebox.showwarning("Aviso", "Não há gravações de Lado A ou Lado B nesta pasta para exportar.")
            return
            
        self.lbl_status.configure(text="EXPORTANDO ÁUDIO DIRETO...", text_color=COLOR_YELLOW)
        self.btn_export.configure(state="disabled")
        self.btn_split.configure(state="disabled")
        meta = self.meta_card.get_metadata()
        
        def task():
            try:
                fmt = self.opt_export.get()
                ext = ".mp3" if "MP3" in fmt else ".flac" if "FLAC" in fmt else ".wav"
                denoise = bool(self.chk_denoise.get())
                normalize = bool(self.chk_normalize.get())
                cover_path = os.path.join(self.project_dir, "cover.jpg")
                
                for name, p in files:
                    data, sr = sf.read(p)
                    duration = len(data) / sr
                    m = meta.copy()
                    album_title = m.get("album", "Vinil")
                    m["title"] = f"{album_title} - {name}"
                    artist_part = f"{m['artist']} - " if m.get("artist") else ""
                    out_name = f"{artist_part}{album_title} - {name}{ext}"
                    out_path = os.path.join(self.project_dir, out_name)
                    self.engine.export_chunk(p, out_path, m, 0.0, duration, fmt, denoise, normalize, cover_path)
                    
                self.after(0, lambda: [
                    self.lbl_status.configure(text="EXPORTAÇÃO CONCLUÍDA COM SUCESSO!", text_color=COLOR_GREEN),
                    self.browser.update_browser(),
                    messagebox.showinfo("Exportação", "Gravação exportada diretamente com sucesso!")
                ])
            except Exception as e:
                self.after(0, lambda: [
                    self.lbl_status.configure(text="ERRO AO EXPORTAR ÁUDIO", text_color=COLOR_RED),
                    messagebox.showerror("Erro", f"Erro ao exportar: {e}")
                ])
            finally:
                self.after(0, lambda: [
                    self.btn_export.configure(state="normal"),
                    self.btn_split.configure(state="normal")
                ])
        threading.Thread(target=task, daemon=True).start()
        
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
                        
                if front_path:
                    self.after(0, lambda: self.cover_front.load_cover(front_path))
                if back_path:
                    self.after(0, lambda: self.cover_back.load_cover(back_path))
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
        self.btn_export.configure(state="disabled")
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

            self.after(0, lambda: [
                self.lbl_status.configure(text=f"SUCESSO! {track_num-1} FAIXAS GERADAS.", text_color=COLOR_GREEN),
                self.browser.update_browser(),
                messagebox.showinfo("Processo Concluído", f"Foram separadas e exportadas {track_num-1} faixas na pasta do projeto.")
            ])
        except Exception as e:
            self.after(0, lambda: self.lbl_status.configure(text="ERRO AO SEPARAR FAIXAS", text_color=COLOR_RED))
            print(e)
        finally:
            self.after(0, lambda: [
                self.btn_split.configure(state="normal"),
                self.btn_export.configure(state="normal")
            ])

    def _ui_update_loop(self):
        if getattr(self.engine, 'trigger_auto_stop', False):
            self.engine.trigger_auto_stop = False
            self.on_stop_click()
            import tkinter.messagebox as messagebox
            messagebox.showinfo("Auto Stop", "A gravação parou automaticamente após 30 segundos de silêncio.")
            
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
            if hasattr(self, 'cover_front') and getattr(self.cover_front, 'camera_active', False):
                self.cover_front.stop_in_place_camera()
            if hasattr(self, 'cover_back') and getattr(self.cover_back, 'camera_active', False):
                self.cover_back.stop_in_place_camera()
            if hasattr(self, 'turntable') and getattr(self.turntable, 'camera_active', False):
                self.turntable.stop_in_place_camera()
            self.engine.stop_stream()
        except:
            pass
        os._exit(0)
if __name__ == "__main__":
    app = App()
    app.mainloop()
