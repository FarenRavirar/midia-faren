import customtkinter as ctk
import os
import subprocess
import threading
import re
import shutil
import glob
from pathlib import Path
from datetime import datetime
from tkinter import filedialog, messagebox

# ================= CONFIGURAÇÕES DE VISUAL =================
ctk.set_appearance_mode("Dark")
ctk.set_default_color_theme("blue")

# ================= CONFIGURAÇÕES DE CAMINHOS =================
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
MODELS_DIR = os.path.join(BASE_DIR, "models")
CONVERTIDOS_DIR = os.path.join(BASE_DIR, "convertidos")
TRANSCRICOES_DIR = os.path.join(BASE_DIR, "transcricoes")
PATH_RELEASE = os.path.join(BASE_DIR, "whisper-cublas-12.4.0-bin-x64", "Release")

# Garante que as pastas de organização existam
os.makedirs(CONVERTIDOS_DIR, exist_ok=True)
os.makedirs(TRANSCRICOES_DIR, exist_ok=True)

class WhisperApp(ctk.CTk):
    def __init__(self):
        super().__init__()

        # Configuração da Janela
        self.title("Whisper GUI - Transcritor NVIDIA")
        self.geometry("950x750") # Aumentei um pouco para caber as dicas
        self.resizable(True, True)

        # Variáveis de Controle
        # Inicializa como None, mas o tipo esperado é str ou None
        self.selected_file = None 
        self.whisper_exe = self.detect_executable()
        self.models = self.list_models()
        self.is_running = False

        # === LAYOUT ===
        self.create_widgets()

    def detect_executable(self):
        possible_names = ["whisper-cli.exe", "main.exe"]
        for name in possible_names:
            path = os.path.join(PATH_RELEASE, name)
            if os.path.exists(path):
                return path
        return None

    def list_models(self):
        if not os.path.exists(MODELS_DIR):
            os.makedirs(MODELS_DIR)
        pattern = os.path.join(MODELS_DIR, "ggml-*.bin")
        models = glob.glob(pattern)
        models.sort()
        return [os.path.basename(m) for m in models] if models else ["Nenhum modelo encontrado"]

    def show_help(self, topic):
        help_texts = {
            "model": "MODELO AI (O Cérebro):\n\n- Tiny/Base: Muito rápidos, menos precisos.\n- Small/Medium: Equilíbrio ideal para a GTX 1650.\n- Large: Máxima precisão, mas lento e pesado.",
            "lang": "IDIOMA:\n\nAjuda a IA a restringir o vocabulário.\n- 'pt': Melhora muito a precisão para português.\n- 'auto': Tenta adivinhar (pode falhar com sotaques).",
            "threads": "THREADS (Processador):\n\nQuantos núcleos da CPU ajudam no processo.\n- Exemplo: Se você tem 6 núcleos, use 4 a 6.\n- Usar 1 deixa lento. Usar o máximo pode travar o PC.",
            "maxlen": "MAX CARACTERES POR LINHA:\n\nDefine o tamanho da legenda.\n- 0: O Whisper decide (pode criar linhas gigantes).\n- 40-50: Padrão de TV/Cinema (fácil leitura).",
            "diar": "DIARIZAÇÃO (Quem fala):\n\n- Ativado: Tenta separar [Speaker 0] e [Speaker 1].\n- Desativado: Texto corrido.\n*Ainda é experimental e pode ser impreciso.*",
            "beam": "BEAM SIZE (Profundidade):\n\nQuantas possibilidades a IA testa para cada palavra.\n- 1: Rápido (pega a 1ª opção).\n- 5: Padrão (testa 5 caminhos).\n- 10: Lento (tenta ser perfeito)."
        }
        messagebox.showinfo(f"Ajuda: {topic.upper()}", help_texts.get(topic, "Sem ajuda disponível."))

    def create_widgets(self):
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(3, weight=1)

        # --- 1. TÍTULO E SELEÇÃO DE ARQUIVO ---
        self.frame_top = ctk.CTkFrame(self)
        self.frame_top.grid(row=0, column=0, padx=20, pady=(20, 10), sticky="ew")
        
        self.lbl_title = ctk.CTkLabel(self.frame_top, text="Transcritor Whisper (GPU)", font=("Roboto", 20, "bold"))
        self.lbl_title.pack(pady=5)

        self.btn_select = ctk.CTkButton(self.frame_top, text="Selecionar Arquivo de Áudio / Vídeo", command=self.select_file, height=40, font=("Roboto", 14))
        self.btn_select.pack(padx=20, pady=10, fill="x")

        self.lbl_file = ctk.CTkLabel(self.frame_top, text="Nenhum arquivo selecionado", text_color="gray")
        self.lbl_file.pack(pady=(0, 10))

        # --- 2. CONFIGURAÇÕES ---
        self.frame_config = ctk.CTkFrame(self)
        self.frame_config.grid(row=1, column=0, padx=20, pady=10, sticky="ew")

        # Função auxiliar para criar labels com '?'
        def create_label_help(parent, text, topic, r, c):
            f = ctk.CTkFrame(parent, fg_color="transparent")
            f.grid(row=r, column=c, padx=5, pady=5, sticky="w")
            ctk.CTkLabel(f, text=text).pack(side="left")
            b = ctk.CTkButton(f, text="?", width=20, height=20, fg_color="gray", command=lambda: self.show_help(topic))
            b.pack(side="left", padx=5)

        # Coluna 1
        create_label_help(self.frame_config, "Modelo AI:", "model", 0, 0)
        self.combo_model = ctk.CTkComboBox(self.frame_config, values=self.models, width=180)
        medium_idx = next((i for i, m in enumerate(self.models) if "medium" in m), 0)
        if self.models: self.combo_model.set(self.models[medium_idx])
        self.combo_model.grid(row=0, column=1, padx=5, pady=5)

        create_label_help(self.frame_config, "Idioma:", "lang", 1, 0)
        self.combo_lang = ctk.CTkComboBox(self.frame_config, values=["pt", "en", "es", "auto"], width=180)
        self.combo_lang.set("pt")
        self.combo_lang.grid(row=1, column=1, padx=5, pady=5)

        # Coluna 2
        create_label_help(self.frame_config, "Threads (CPU):", "threads", 0, 2)
        self.entry_threads = ctk.CTkEntry(self.frame_config, width=60)
        self.entry_threads.insert(0, "6")
        self.entry_threads.grid(row=0, column=3, padx=5, pady=5, sticky="w")

        create_label_help(self.frame_config, "Max Caracteres:", "maxlen", 1, 2)
        self.entry_maxlen = ctk.CTkEntry(self.frame_config, width=60)
        self.entry_maxlen.insert(0, "0")
        self.entry_maxlen.grid(row=1, column=3, padx=5, pady=5, sticky="w")

        # Coluna 3
        create_label_help(self.frame_config, "Diarização:", "diar", 0, 4)
        self.switch_diarize = ctk.CTkSwitch(self.frame_config, text="") # Texto vazio pois o label já diz
        self.switch_diarize.grid(row=0, column=5, padx=5, pady=5, sticky="w")
        
        create_label_help(self.frame_config, "Beam Size:", "beam", 1, 4)
        self.slider_beam = ctk.CTkSlider(self.frame_config, from_=1, to=10, number_of_steps=9)
        self.slider_beam.set(5)
        self.slider_beam.grid(row=1, column=5, padx=5, pady=5, sticky="ew")

        # --- 3. PROGRESSO E LOG ---
        self.progress_bar = ctk.CTkProgressBar(self)
        self.progress_bar.set(0)
        self.progress_bar.grid(row=2, column=0, padx=20, pady=(10, 0), sticky="ew")

        self.textbox_log = ctk.CTkTextbox(self, font=("Consolas", 12))
        self.textbox_log.grid(row=3, column=0, padx=20, pady=10, sticky="nsew")
        self.textbox_log.insert("0.0", "=== AGUARDANDO INÍCIO ===\nSelecione um arquivo para começar.\n")

        # --- 4. BOTÃO ---
        self.btn_run = ctk.CTkButton(self, text="INICIAR TRANSCRIÇÃO", command=self.start_thread, height=50, fg_color="green", hover_color="darkgreen", font=("Roboto", 16, "bold"))
        self.btn_run.grid(row=4, column=0, padx=20, pady=20, sticky="ew")

    def select_file(self):
        file_path = filedialog.askopenfilename(
            title="Selecione o arquivo de mídia",
            filetypes=[("Arquivos de Mídia", "*.mp3 *.wav *.m4a *.mp4 *.flac *.ogg *.mkv"), ("Todos os arquivos", "*.*")]
        )
        if file_path:
            self.selected_file = file_path
            self.lbl_file.configure(text=f"Selecionado: {os.path.basename(file_path)}", text_color="cyan")
            self.log(f"Arquivo carregado: {file_path}")

    def log(self, message):
        self.textbox_log.insert("end", message + "\n")
        self.textbox_log.see("end")

    def start_thread(self):
        if not self.selected_file:
            messagebox.showwarning("Aviso", "Por favor, selecione um arquivo primeiro.")
            return
        
        if not self.whisper_exe:
            messagebox.showerror("Erro", "Executável (main.exe/whisper-cli.exe) não encontrado!")
            return

        if self.is_running:
            return

        self.is_running = True
        self.btn_run.configure(state="disabled", text="PROCESSANDO...")
        self.progress_bar.set(0)
        
        thread = threading.Thread(target=self.run_process)
        thread.start()

    def run_process(self):
        try:
            # CORREÇÃO PYLANCE 1:
            # Atribui a uma variável local e verifica se é None.
            # Isso garante ao verificador de tipos que 'arquivo_atual' é uma String segura.
            arquivo_atual = self.selected_file
            if arquivo_atual is None:
                self.log("Erro: Nenhum arquivo selecionado.")
                return

            # 1. Preparação de Pastas e Nomes
            timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
            original_stem = Path(arquivo_atual).stem
            
            wav_filename = f"{original_stem}_{timestamp}.wav"
            wav_path = os.path.join(CONVERTIDOS_DIR, wav_filename)

            model_name = self.combo_model.get()
            model_path = os.path.join(MODELS_DIR, model_name)
            
            # 2. Conversão
            self.log(f"\n[ETAPA 1/2] Convertendo para pasta \\convertidos...")
            
            duracao = 100.0
            try:
                cmd_dur = ['ffprobe', '-v', 'error', '-show_entries', 'format=duration', '-of', 'default=noprint_wrappers=1:nokey=1', arquivo_atual]
                startupinfo = subprocess.STARTUPINFO()
                startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
                dur = subprocess.check_output(cmd_dur, startupinfo=startupinfo)
                duracao = float(dur)
            except:
                pass

            cmd_conv = ['ffmpeg', '-i', arquivo_atual, '-ar', '16000', '-ac', '1', '-c:a', 'pcm_s16le', '-y', wav_path]
            startupinfo = subprocess.STARTUPINFO()
            startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
            subprocess.run(cmd_conv, startupinfo=startupinfo, stderr=subprocess.PIPE)

            # 3. Transcrição
            self.log(f"[ETAPA 2/2] Iniciando Whisper ({model_name})...")
            
            cmd_whisper = [
                self.whisper_exe,
                '-m', model_path,
                '-f', wav_path,
                '-l', self.combo_lang.get(),
                '-t', self.entry_threads.get(),
                '-bs', str(int(self.slider_beam.get())),
                '-ml', self.entry_maxlen.get(),
                '-osrt'
            ]
            if self.switch_diarize.get():
                cmd_whisper.append('-tdrz')

            process = subprocess.Popen(
                cmd_whisper,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                universal_newlines=True,
                encoding='utf-8',
                errors='replace',
                startupinfo=startupinfo
            )

            padrao_timestamp = re.compile(r"\[(\d{2}):(\d{2}):(\d{2}\.\d{3}) --> (\d{2}):(\d{2}):(\d{2}\.\d{3})\]")
            
            # CORREÇÃO PYLANCE 2:
            # Verifica se process.stdout não é None antes de tentar fazer o loop.
            if process.stdout is not None:
                for line in process.stdout:
                    line = line.strip()
                    if not line or "WARNING" in line: continue
                    
                    self.log(line)
                    
                    match = padrao_timestamp.search(line)
                    if match:
                        h, m, s = float(match.group(4)), float(match.group(5)), float(match.group(6))
                        current_seconds = h*3600 + m*60 + s
                        progress = current_seconds / duracao
                        self.progress_bar.set(progress)

            process.wait()

            # 4. Finalização e Organização
            final_base_name = f"{original_stem}_{timestamp}"
            
            srt_gerado = wav_path + ".srt"
            if not os.path.exists(srt_gerado):
                srt_gerado = os.path.splitext(wav_path)[0] + ".srt"
            
            final_srt_path = os.path.join(TRANSCRICOES_DIR, f"{final_base_name}.srt")
            
            if os.path.exists(srt_gerado):
                shutil.move(srt_gerado, final_srt_path)
                self.log(f"\n[SUCESSO] Legenda movida para: {final_srt_path}")

            final_txt_path = os.path.join(TRANSCRICOES_DIR, f"{final_base_name}.txt")
            with open(final_txt_path, "w", encoding="utf-8") as f:
                f.write(self.textbox_log.get("1.0", "end"))
            self.log(f"[SUCESSO] Log salvo em: {final_txt_path}")
            
            messagebox.showinfo("Concluído", "Transcrição finalizada com sucesso!")
            os.startfile(final_txt_path)

        except Exception as e:
            self.log(f"\n[ERRO FATAL]: {str(e)}")
            messagebox.showerror("Erro", str(e))
        
        finally:
            self.is_running = False
            self.btn_run.configure(state="normal", text="INICIAR TRANSCRIÇÃO")
            self.progress_bar.set(0)

if __name__ == "__main__":
    app = WhisperApp()
    app.mainloop()