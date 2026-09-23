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

    def export_chunk(self, wav_path: str, out_path: str, metadata: dict, start_time: float, duration: float, fmt: str, denoise: bool):
        cmd = ["ffmpeg", "-y", "-i", wav_path, "-ss", str(start_time), "-t", str(duration)]
        
        if denoise:
            cmd.extend(["-af", "afftdn=nf=-25"])
            
        if "MP3" in fmt:
            cmd.extend(["-codec:a", "libmp3lame", "-qscale:a", "2"])
        elif "FLAC" in fmt:
            cmd.extend(["-codec:a", "flac"])
        else:
            cmd.extend(["-codec:a", "pcm_s16le"])
            
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
    def __init__(self, master, **kwargs):
        super().__init__(master, fg_color=COLOR_SURFACE, corner_radius=0, border_width=1, border_color=COLOR_SURFACE2, **kwargs)
        
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
            query = urllib.parse.quote(f"{artist} {album}")
            url = f"https://itunes.apple.com/search?term={query}&entity=album&limit=1"
            req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
            with urllib.request.urlopen(req) as response:
                data = json.loads(response.read().decode())
                
            if data["resultCount"] > 0:
                res = data["results"][0]
                self.entries["artist"].delete(0, 'end')
                self.entries["artist"].insert(0, res.get("artistName", artist))
                self.entries["album"].delete(0, 'end')
                self.entries["album"].insert(0, res.get("collectionName", album))
                
                year = res.get("releaseDate", "")[:4]
                if year:
                    self.entries["year"].delete(0, 'end')
                    self.entries["year"].insert(0, year)
                    
                messagebox.showinfo("Sucesso", f"Álbum encontrado na base de dados global!\n\n{res.get('collectionName')} ({year})")
            else:
                messagebox.showinfo("Não encontrado", "Não encontramos esse álbum exato na base de dados.")
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
        self.geometry("900x720")
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
        
        mid_frame = customtkinter.CTkFrame(self, fg_color="transparent")
        mid_frame.pack(fill="x", padx=10, pady=5)
        
        dev_frame = customtkinter.CTkFrame(mid_frame, fg_color=COLOR_SURFACE, corner_radius=0, border_width=1, border_color=COLOR_SURFACE2)
        dev_frame.pack(side="left", fill="both", expand=True, padx=(0, 5))
        customtkinter.CTkLabel(dev_frame, text="[ ROTEAMENTO ]", font=FONT_BOLD, text_color=COLOR_TEXT2).pack(pady=(10, 5))
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
        
        self.meta_card = MetadataCard(mid_frame)
        self.meta_card.pack(side="right", fill="both", expand=True, padx=(5, 0))
        
        vis_frame = customtkinter.CTkFrame(self, fg_color=COLOR_SURFACE, corner_radius=0, border_width=1, border_color=COLOR_SURFACE2)
        vis_frame.pack(fill="x", padx=10, pady=10)
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
        
        p_bot = customtkinter.CTkFrame(proj_frame, fg_color="transparent")
        p_bot.pack(fill="x", padx=15, pady=10)
        
        opt_frame = customtkinter.CTkFrame(p_bot, fg_color="transparent")
        opt_frame.pack(fill="x", pady=(0, 10))
        
        customtkinter.CTkLabel(opt_frame, text="Exportar em:", font=FONT_BOLD, text_color=COLOR_TEXT2).pack(side="left")
        self.opt_format = customtkinter.CTkOptionMenu(opt_frame, values=["MP3 (Padrão)", "FLAC (Lossless)", "WAV (Original)"], fg_color=COLOR_BG, button_color=COLOR_SURFACE2, font=FONT_MAIN)
        self.opt_format.pack(side="left", padx=10)
        
        self.chk_denoise = customtkinter.CTkCheckBox(opt_frame, text="Filtro Anti-Chiado (De-noise)", fg_color=COLOR_ACCENT, text_color=COLOR_TEXT, font=FONT_MAIN)
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
                
        self.engine.start_recording(wav_path)
        self.btn_rec_a.configure(state="disabled")
        self.btn_rec_b.configure(state="disabled")
        self.btn_split.configure(state="disabled")
        self.btn_stop.configure(state="normal", fg_color=COLOR_ACCENT)
        self.lbl_status.configure(text=f"GRAVANDO {side_name.upper()}...", text_color=COLOR_RED)
        
    def on_stop_click(self):
        self.engine.stop_recording()
        self.btn_rec_a.configure(state="normal")
        self.btn_rec_b.configure(state="normal")
        self.btn_split.configure(state="normal")
        self.btn_stop.configure(state="disabled", fg_color=COLOR_SURFACE2)
        self.lbl_status.configure(text="GRAVAÇÃO FINALIZADA. PRONTO.", text_color=COLOR_GREEN)
        
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

                # Exportar chunks
                for start_t, end_t in faixas_timestamps:
                    duration = end_t - start_t
                    if duration < 10.0: continue # Ignora barulhos curtos perdidos
                    
                    track_meta = meta.copy()
                    track_meta["track"] = str(track_num)
                    if not track_meta.get("title"): track_meta["title"] = f"Faixa {track_num}"
                    else: track_meta["title"] = f"{track_meta['title']} {track_num}"
                        
                    out_path = os.path.join(self.project_dir, f"{track_num:02d} - {track_meta['title']}{ext}")
                    self.engine.export_chunk(wav_path, out_path, track_meta, start_t, duration, fmt, denoise)
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
