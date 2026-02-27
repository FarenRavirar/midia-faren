import os
import sys
import subprocess
import re
import time
import glob
import shutil
from tqdm import tqdm
from pathlib import Path
from datetime import datetime

# ================= CONFIGURAÇÕES DE CAMINHOS =================
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
MODELS_DIR = os.path.join(BASE_DIR, "models")
CONVERTIDOS_DIR = os.path.join(BASE_DIR, "convertidos")
TRANSCRICOES_DIR = os.path.join(BASE_DIR, "transcricoes")

os.makedirs(CONVERTIDOS_DIR, exist_ok=True)
os.makedirs(TRANSCRICOES_DIR, exist_ok=True)

# --- DETECÇÃO AUTOMÁTICA DO EXECUTÁVEL ---
path_release = os.path.join(BASE_DIR, "whisper-cublas-12.4.0-bin-x64", "Release")
possible_names = ["whisper-cli.exe", "main.exe"]
WHISPER_EXE = None
WHISPER_DIR = path_release # Guardamos a pasta para verificar as DLLs depois

for name in possible_names:
    potential_path = os.path.join(path_release, name)
    if os.path.exists(potential_path):
        WHISPER_EXE = potential_path
        break

# ================= FUNÇÕES DE DIAGNÓSTICO (NOVO) =================
def verificar_ffmpeg():
    """Verifica se o FFmpeg está instalado e acessível."""
    if shutil.which("ffmpeg") is None:
        print("\n[ERRO CRÍTICO] FFmpeg não encontrado no sistema!")
        print("Solução: Baixe o FFmpeg, extraia e adicione a pasta 'bin' ao PATH do Windows.")
        print("Sem isso, a conversão de áudio é impossível.")
        return False
    return True

def verificar_dlls_nvidia():
    """Verifica se as DLLs essenciais para GPU estão junto com o executável."""
    if not WHISPER_DIR or not os.path.exists(WHISPER_DIR):
        return True # Se não achou a pasta, o erro de executável vai pegar depois

    dlls_necessarias = ["cublas64_12.dll", "cudart64_12.dll", "cublasLt64_12.dll"]
    faltando = []
    
    for dll in dlls_necessarias:
        caminho_dll = os.path.join(WHISPER_DIR, dll)
        if not os.path.exists(caminho_dll):
            faltando.append(dll)
    
    if faltando:
        print("\n[ERRO CRÍTICO] DLLs da NVIDIA faltando!")
        print(f"O executável está em: {WHISPER_DIR}")
        print("Mas faltam estes arquivos na MESMA pasta:")
        for f in faltando:
            print(f" - {f}")
        print("\nSolução: Extraia TUDO do zip 'whisper-cublas-12.4.0-bin-x64.zip' novamente.")
        return False
    return True

def verificar_arquivo_wav(caminho_wav):
    """Verifica se a conversão funcionou ou gerou arquivo vazio."""
    if not os.path.exists(caminho_wav):
        print(f"\n[ERRO] O arquivo {os.path.basename(caminho_wav)} não foi criado.")
        return False
    
    tamanho = os.path.getsize(caminho_wav)
    if tamanho < 1024: # Menor que 1KB
        print(f"\n[ERRO] O arquivo convertido está corrompido (Tamanho: {tamanho} bytes).")
        print("Possível causa: FFmpeg falhou ou o áudio original está protegido/corrompido.")
        return False
    return True

# ================= FUNÇÕES UTILITÁRIAS =================

def limpar_tela():
    os.system('cls' if os.name == 'nt' else 'clear')

def listar_modelos_disponiveis():
    pattern = os.path.join(MODELS_DIR, "ggml-*.bin")
    modelos = glob.glob(pattern)
    modelos.sort()
    return modelos

def obter_duracao_audio(arquivo):
    try:
        cmd = ['ffprobe', '-v', 'error', '-show_entries', 'format=duration', '-of', 'default=noprint_wrappers=1:nokey=1', arquivo]
        startupinfo = subprocess.STARTUPINFO()
        startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        resultado = subprocess.check_output(cmd, startupinfo=startupinfo)
        return float(resultado)
    except Exception:
        return None

def converter_audio(entrada, saida_wav):
    # 1. Diagnóstico Prévio
    if not verificar_ffmpeg(): return False

    duracao_total = obter_duracao_audio(entrada)
    if not duracao_total: duracao_total = 100 

    cmd = ['ffmpeg', '-i', entrada, '-ar', '16000', '-ac', '1', '-c:a', 'pcm_s16le', '-y', saida_wav]

    print(f"\n[1/2] Convertendo Áudio para pasta \\convertidos...")
    
    startupinfo = subprocess.STARTUPINFO()
    startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    
    try:
        processo = subprocess.Popen(cmd, stderr=subprocess.PIPE, universal_newlines=True, startupinfo=startupinfo)
        
        barra = tqdm(total=duracao_total, unit="s", bar_format="{l_bar}{bar}| {n:.0f}/{total:.0f}s")
        padrao_tempo = re.compile(r"time=(\d{2}):(\d{2}):(\d{2}\.\d{2})")
        ultimo_tempo = 0
        
        if processo.stderr is not None:
            for linha in processo.stderr:
                match = padrao_tempo.search(linha)
                if match:
                    h, m, s = map(float, match.groups())
                    tempo_atual = h*3600 + m*60 + s
                    incremento = tempo_atual - ultimo_tempo
                    if incremento > 0:
                        barra.update(incremento)
                        ultimo_tempo = tempo_atual
        
        barra.close()
        processo.wait()
    except Exception as e:
        print(f"\n[ERRO] Falha ao executar FFmpeg: {e}")
        return False

    # 2. Diagnóstico Pós-Conversão
    if not verificar_arquivo_wav(saida_wav):
        return False

    return processo.returncode == 0

def menu_configuracao(config_atual):
    while True:
        limpar_tela()
        print("=== AJUSTE FINO & EXPLICAÇÃO ===")
        print("-" * 70)
        
        # Opções de configuração mantidas como você gosta
        print(f"1. Max Line Length (Atual: {config_atual['max_len']})")
        print(f"   > 40-50 caracteres é ideal para legendas.")
        print("-" * 70)
        print(f"2. Idioma (Atual: {config_atual['language']})")
        print("-" * 70)
        status_diar = "ATIVADO" if config_atual['diarize'] else "DESATIVADO"
        print(f"3. Diarização (Atual: {status_diar})")
        print("-" * 70)
        print(f"4. Threads CPU (Atual: {config_atual['threads']})")
        print("-" * 70)
        print(f"5. Beam Size (Atual: {config_atual['beam_size']})")
        print("-" * 70)
        
        print("\n[ENTER] Voltar e Salvar | Digite o número para alterar")

        escolha = input("Opção: ").strip()
        if escolha == "": break
        
        elif escolha == "1":
            ml = input(">> Novo Max Len (ex: 42): ").strip()
            if ml.isdigit(): config_atual['max_len'] = ml
        elif escolha == "2":
            sub = input("1. Pt | 2. En | 3. Auto: ").strip()
            if sub == "1": config_atual['language'] = "pt"
            elif sub == "2": config_atual['language'] = "en"
            elif sub == "3": config_atual['language'] = "auto"
        elif escolha == "3":
            resp = input(">> Ativar Diarização? (s/n): ").lower()
            config_atual['diarize'] = True if resp == 's' else False
        elif escolha == "4":
            t = input(">> Threads: ").strip()
            if t.isdigit(): config_atual['threads'] = t
        elif escolha == "5":
            b = input(">> Beam size: ").strip()
            if b.isdigit(): config_atual['beam_size'] = b

    return config_atual

def transcrever_whisper(arquivo_wav, nome_original, duracao_total, config):
    # --- FUNÇÃO AUXILIAR PARA TEMPO (SEGUNDOS -> HH:MM:SS) ---
    def formatar_tempo(segundos):
        return time.strftime('%H:%M:%S', time.gmtime(segundos))

    # 1. Diagnóstico Prévio de GPU
    if not verificar_dlls_nvidia():
        print("\n[ABORTANDO] Processo cancelado para evitar travamento.")
        input("Pressione ENTER para sair...")
        sys.exit(1)

    # 2. Configuração de Nomes e Caminhos
    timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    nome_limpo = Path(nome_original).stem
    modelo_tag = os.path.basename(config['model']).replace("ggml-", "").replace(".bin", "")
    nome_base_final = f"{nome_limpo}_{timestamp}_{modelo_tag}"
    caminho_absoluto_log = os.path.join(TRANSCRICOES_DIR, f"{nome_base_final}.txt")

    # 3. CRÍTICO: Cria o arquivo VAZIO agora para o monitor existir
    with open(caminho_absoluto_log, "w", encoding="utf-8") as f:
        f.write(f"=== INICIO DA TRANSCRIÇÃO: {timestamp} ===\n")
        f.write(f"Modelo: {modelo_tag} | Diarização: {config['diarize']}\n")
        f.write("==========================================\n\n")

    # 4. Montagem do Comando (com Quebra Inteligente -sow)
    cmd = [
        WHISPER_EXE, 
        '-m', config['model'], 
        '-f', arquivo_wav,
        '-l', config['language'], 
        '-t', str(config['threads']),
        '-bs', str(config['beam_size']),
        '-ml', str(config['max_len']),
        '-sow',  
        '-osrt'
    ]
    if config['diarize']: cmd.append('-tdrz')

    # 5. MONITOR POWER_SHELL (Sintaxe Simplificada para não crashar)
    # Abre, força UTF8, lê o arquivo que acabamos de criar, espera 60s e fecha (exit)
    comando_monitor = (
        f'start "Monitor [{modelo_tag}]" powershell -NoExit -Command "'
        f'$OutputEncoding = [System.Text.Encoding]::UTF8; '
        f'Get-Content \'{caminho_absoluto_log}\' -Wait -Encoding UTF8; '
        f'Start-Sleep -Seconds 60; exit"'
    )
    subprocess.Popen(comando_monitor, shell=True)

    print(f"\n[2/2] Transcrevendo ({modelo_tag})...")
    
    # 6. Execução do Whisper com Barra de Progresso Formatada
    startupinfo = subprocess.STARTUPINFO()
    startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    
    try:
        processo = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, 
                                   universal_newlines=True, encoding='utf-8', errors='replace', startupinfo=startupinfo)
        
        # BARRA DE PROGRESSO COM MONITOR DE TEMPO E ETA
        str_total = formatar_tempo(duracao_total)
        barra = tqdm(total=duracao_total, unit="s", bar_format="{desc} | {percentage:3.0f}% | Falta: {postfix}")
        barra.set_description_str(f"{formatar_tempo(0)} / {str_total}")

        padrao_timestamp = re.compile(r"\[(\d{2}):(\d{2}):(\d{2}\.\d{3}) --> (\d{2}):(\d{2}):(\d{2}\.\d{3})\]")
        ultimo_tempo = 0
        start_time = time.time()
        
        if processo.stdout is not None:
            falante_atual = 1
            ultimo_tempo_fim = 0.0
            texto_buffer = []
            chars_no_bloco = 0
            tempo_inicio_bloco_sec = None

            for linha in processo.stdout:
                if "WARNING" in linha:
                    continue

                match = padrao_timestamp.search(linha)
                if match:
                    h1, m1, s1 = float(match.group(1)), float(match.group(2)), float(match.group(3))
                    h2, m2, s2 = float(match.group(4)), float(match.group(5)), float(match.group(6))

                    tempo_inicio_atual = h1 * 3600 + m1 * 60 + s1
                    tempo_fim_atual = h2 * 3600 + m2 * 60 + s2

                    texto_da_fala = linha.split("]")[-1].strip()
                    if not texto_da_fala:
                        continue

                    if not texto_buffer:
                        tempo_inicio_bloco_sec = tempo_inicio_atual
                    else:
                        intervalo = tempo_inicio_atual - ultimo_tempo_fim
                        if intervalo > 0.4:
                            linha_formatada = (
                                f"[{formatar_tempo(tempo_inicio_bloco_sec)} --> {formatar_tempo(ultimo_tempo_fim)}] "
                                f"Falante {falante_atual} — {' '.join(texto_buffer)}\n"
                            )

                            with open(caminho_absoluto_log, "a", encoding="utf-8") as f:
                                f.write(linha_formatada)
                                f.flush()

                            falante_atual = 2 if falante_atual == 1 else 1
                            texto_buffer = []
                            chars_no_bloco = 0
                            tempo_inicio_bloco_sec = ultimo_tempo_fim

                    if texto_buffer:
                        chars_no_bloco += 1 + len(texto_da_fala)
                    else:
                        chars_no_bloco += len(texto_da_fala)
                    texto_buffer.append(texto_da_fala)

                    duracao_bloco = tempo_fim_atual - (tempo_inicio_bloco_sec or tempo_inicio_atual)
                    if chars_no_bloco >= 300 or duracao_bloco >= 30:
                        linha_formatada = (
                            f"[{formatar_tempo(tempo_inicio_bloco_sec)} --> {formatar_tempo(tempo_fim_atual)}] "
                            f"Falante {falante_atual} — {' '.join(texto_buffer)}\n"
                        )

                        with open(caminho_absoluto_log, "a", encoding="utf-8") as f:
                            f.write(linha_formatada)
                            f.flush()

                        texto_buffer = []
                        chars_no_bloco = 0
                        tempo_inicio_bloco_sec = tempo_fim_atual

                    ultimo_tempo_fim = tempo_fim_atual

                    if tempo_fim_atual > ultimo_tempo:
                        incremento = tempo_fim_atual - ultimo_tempo
                        barra.update(incremento)
                        ultimo_tempo = tempo_fim_atual

                        tempo_passado_real = time.time() - start_time
                        progresso_no_audio = max(ultimo_tempo, 1e-6)
                        restante_audio = max(duracao_total - ultimo_tempo, 0.0)
                        eta_segundos = (tempo_passado_real / progresso_no_audio) * restante_audio

                        barra.set_description_str(f"{formatar_tempo(ultimo_tempo)} / {str_total}")
                        barra.set_postfix_str(formatar_tempo(eta_segundos))

            if texto_buffer:
                linha_final = (
                    f"[{formatar_tempo(tempo_inicio_bloco_sec)} --> {formatar_tempo(ultimo_tempo_fim)}] "
                    f"Falante {falante_atual} — {' '.join(texto_buffer)}\n"
                )
                with open(caminho_absoluto_log, "a", encoding="utf-8") as f:
                    f.write(linha_final)
                    f.flush()


        
        processo.wait()
        barra.close()
        
    except Exception as e:
        print(f"\n[ERRO CRÍTICO NA TRANSCRIÇÃO]: {e}")
        return None, None

    # 7. Mover arquivo SRT
    arquivo_srt_gerado = arquivo_wav + ".srt"
    if not os.path.exists(arquivo_srt_gerado):
        arquivo_srt_gerado = os.path.splitext(arquivo_wav)[0] + ".srt"

    caminho_final_srt = os.path.join(TRANSCRICOES_DIR, f"{nome_base_final}.srt")

    if os.path.exists(arquivo_srt_gerado):
        try:
            shutil.move(arquivo_srt_gerado, caminho_final_srt)
        except Exception as e:
            print(f"Erro ao mover SRT: {e}")

    return caminho_absoluto_log, caminho_final_srt

def main():
    limpar_tela()
    
    # 1. TESTE INICIAL
    if not WHISPER_EXE:
        print(f"[ERRO CRÍTICO] Executável não encontrado em: {path_release}")
        sys.exit(1)
    
    # Verifica DLLs logo de cara
    if not verificar_dlls_nvidia():
        print("\nO programa não pode continuar sem as DLLs acima.")
        input("Pressione ENTER para fechar este prompt...")
        sys.exit(0) # Sai de forma limpa

    modelos_locais = listar_modelos_disponiveis()
    if not modelos_locais:
        print(f"[ERRO] Nenhum modelo .bin encontrado na pasta '{MODELS_DIR}'"); return

    index_padrao = next((i for i, m in enumerate(modelos_locais) if "medium" in m), 0)
    
    # 2. Seleção de Arquivo
    caminho_input = input("\nArraste o arquivo de áudio para cá:\n> ").strip('"')
    if not os.path.exists(caminho_input):
        print("Arquivo não encontrado."); return

    # 3. Seleção de Modelo
    print("\n--- SELECIONE O MODELO ---")
    for i, mod in enumerate(modelos_locais):
        print(f"{i+1}. {os.path.basename(mod)}{' [PADRÃO]' if i == index_padrao else ''}")
    
    escolha_modelo = input("\n[ENTER] Padrão | [Número] Específico: ").strip()
    
    modelo_final = modelos_locais[index_padrao]
    if escolha_modelo.isdigit() and 0 <= int(escolha_modelo)-1 < len(modelos_locais):
        modelo_final = modelos_locais[int(escolha_modelo)-1]

    # Ajuste nas configurações iniciais para melhor precisão
    config = {
        'model': modelo_final,
        'language': 'pt',
        'threads': '6',
        'beam_size': '5',
        'diarize': True,  # Alterado para True como padrão para testar falantes
        'max_len': '42'   # Alterado de 0 para 42 para ativar a quebra de linha inteligente
    }

    # 4. Conversão (COM TESTE DE INTEGRIDADE)
    timestamp_conv = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    nome_base_orig = Path(caminho_input).stem
    nome_wav_final = f"{nome_base_orig}_{timestamp_conv}.wav"
    caminho_wav_convertido = os.path.join(CONVERTIDOS_DIR, nome_wav_final)

    # Se a conversão falhar, aborta tudo
    if not converter_audio(caminho_input, caminho_wav_convertido):
        print("[ABORTANDO] Erro na conversão de áudio.")
        input("Pressione ENTER...")
        return

    duracao = obter_duracao_audio(caminho_wav_convertido)
    if not duracao: duracao = 100

# 5. Checkpoint
    while True:
        limpar_tela()
        print("=== CHECKPOINT: CONFIGURAÇÃO ===")
        print(f"Arquivo: {os.path.basename(caminho_input)}")
        print("-" * 60)
        print(f"Modelo Atual: {os.path.basename(config['model'])}")
        print(f"Idioma:       {config['language']}")
        print(f"Threads:      {config['threads']}")
        print("-" * 60)
        print(" [ENTER] Iniciar Transcrição Única")
        print(" [C]     MODO COMPARAÇÃO (Testar todos os modelos da pasta)")
        print(" [N]     Configurar | [R] Reiniciar | [X] Sair")
        
        decisao = input(">> ").lower().strip()

        if decisao == 'n': 
            config = menu_configuracao(config)
        elif decisao == 'r':
            main()
            return
        elif decisao == 'x':
            sys.exit(0)
        elif decisao == 'c':
            print(f"\n[MODO COMPARAÇÃO ATIVADO]")
            print(f"Testando {len(modelos_locais)} modelos sequencialmente...")
            
            for mod_path in modelos_locais:
                # Atualiza o modelo na configuração para este turno
                config['model'] = mod_path
                m_nome = os.path.basename(mod_path)
                
                print(f"\n" + "="*40)
                print(f" RODANDO MODELO: {m_nome}")
                print("="*40)
                
                # Executa a transcrição (o sufixo será aplicado automaticamente pela função)
                transcrever_whisper(caminho_wav_convertido, caminho_input, duracao, config)
            
            print("\n" + "="*60)
            print("COMPARAÇÃO CONCLUÍDA COM SUCESSO!")
            print(f"Arquivos salvos em: {TRANSCRICOES_DIR}")
            input("\nPressione ENTER para voltar ao menu...")
            main()
            return
        else:
            break # Segue para transcrição única padrão

    # 6. Transcrição
    arq_txt, arq_srt = transcrever_whisper(caminho_wav_convertido, caminho_input, duracao, config)
    
    if arq_txt:
        print(f"\n\n=== CONCLUÍDO ===")
        print(f"TXT Salvo em: {arq_txt}")
        print(f"SRT Salvo em: {arq_srt}")
        os.startfile(arq_txt)
    else:
        print("\n[FALHA] Não foi possível gerar a transcrição.")

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n\n[AVISO] Operação interrompida pelo usuário.")
        print("Finalizando prompt com segurança...")
        # Código de saída 0 evita a pergunta "Deseja finalizar o arquivo em lotes (S/N)?" em muitos terminais
        sys.exit(0)
