# 💽 Open Vynil Ripper — Analog Edition (v2.0)

[![Release](https://img.shields.io/badge/Release-v2.0.0-orange.svg)](https://github.com/conrider3000/OpenVynilRipper/releases/tag/v2.0.0)
[![Python](https://img.shields.io/badge/Python-3.10%2B-blue.svg)](https://python.org)
[![Platform](https://img.shields.io/badge/Platform-Windows-lightgrey.svg)](https://microsoft.com/windows)
[![License](https://img.shields.io/badge/License-MIT-green.svg)](#)

O **Open Vynil Ripper** é um aplicativo moderno e elegante desenvolvido em Python e CustomTkinter para captura, monitoramento, equalização em tempo real e digitalização de discos de vinil através de toca-discos USB (como os modelos da **ION Audio** e compatíveis).

Diferente do software legado original (EZ Vinyl / Tape Converter), o Open Vynil Ripper elimina limitações históricas: permite **roteamento de áudio em tempo real com baixa latência**, **equalização de 3 vias**, **VU meters analógicos estéreo duplos**, **gerenciamento de capas via câmera embutida ou arquivo**, **busca de metadados integrada** e **separação inteligente de faixas**.

---

## 🌟 O que há de novo na Versão 2.0 (Changelog V2.0)

A versão 2.0 representa uma evolução completa de design e arquitetura:

- ☀️ / 🌙 **Suporte a Temas Dia e Noite**:
  - **Tema Noite (Dark)**: Inspirado em equipamentos analógicos de estúdio vintage com superfícies escuras e toques âmbar/laranja.
  - **Tema Dia (Light)**: Interface moderna em tons off-white elegantes de alto contraste e fácil leitura diurna.
- ⇕ **Minimizar / Maximizar Global (`EXPANDIR TODOS` / `RECOLHER TODOS`)**:
  - Botão no cabeçalho superior que recolhe ou expande instantaneamente todos os módulos do painel.
  - Ao recolher os painéis de controle, a área central do toca-discos e das capas do álbum se expande automaticamente aproveitando todo o monitor.
- 📐 **Interface Alinhada e Padronizada**:
  - Bordas curvas com raio de 8px e linhas de contorno em todos os módulos abertos e fechados.
  - Alturas rigorosamente equalizadas:
    - **Painel Central de Áudio**: VU de entrada, Waveform, Equalizador de 3 vias e VU de monitoramento milimetricamente nivelados em 140px.
    - **Painel Inferior**: Módulo do Gravador e Módulo "Meu Disco" nivelados com precisão em 226px, sem espaços cinzas vazios indesejados.
- 🎛️ **Gravador de Estúdio Redesenhado**:
  - Contador e timer em tempo real (`00:00.00`) posicionado acima dos botões de controle de transporte.
  - Botões de ação (`● GRAVAR LADO A`, `● GRAVAR LADO B`, `⏸ PAUSE`, `■ STOP`) uniformizados e padronizados.
  - Lógica inteligente de botões: exibe **GRAVAR** no início da sessão e muda para **REGRAVAR** apenas se o respectivo lado tiver sido gravado na sessão atual.
  - Separação clara entre configurações de gravação no topo (*Magic Record*, *Remover Clippings*, formato original) e opções de exportação na base.
- 🔇 **Silenciar / Mutar Entrada**:
  - Botão de mute integrado ao lado do seletor da entrada de gravação para silenciar microfones indesejados quando o toca-discos estiver desligado.
- 📸 **Captura Inteligente de Capas (Câmera Integrada)**:
  - Diálogo de confirmação para escolher entre **Tirar Foto com a Câmera** ou **Carregar do Computador**.
  - Visualização da câmera espelhada horizontalmente (modo espelho natural) renderizada diretamente no quadro da capa, desligando a webcam com segurança ao bater a foto.
- 📂 **Mini Explorador de Disco Funcional**:
  - Visualização em tempo real dos arquivos gerados (`.wav`, `.mp3`, `.flac`, capas `cover.jpg`, `back.jpg`, `label.jpg`).
  - Botões para criar novas pastas, subir diretórios e abrir diretamente a pasta no Windows Explorer.

---

## 🎧 Principais Recursos

- **Roteamento e Monitoramento em Tempo Real**: Ouça o vinil pelos fones ou alto-falantes do PC enquanto grava, sem atraso perceptível.
- **Equalizador Analógico de 3 Bandas**: Ajuste de Graves (*Bass*), Médios (*Mid*) e Agudos (*Treble*) antes de salvar o áudio.
- **Waveform Retrô & Medidores VU**: Visualizadores inspirados em equipamentos de estúdio analógicos com balística suave e indicação estéreo de pico (L/R).
- **Magic Record**: Início automático da gravação assim que a agulha toca o vinil e o áudio começa a tocar.
- **Separação Automática de Faixas**: Algoritmo inteligente que detecta silêncios entre músicas e divide o arquivo em faixas individuais nomeadas.
- **Normalização e Filtro Anti-Chiado**: Filtros integrados de redução de ruído de vinil e ajuste automático de ganho (Loudness Normalization).
- **Exportação Multiformato**: Salve em **MP3**, **FLAC**, **WAV** ou **OGG** com tags ID3 e capa embutida.

---

## 🚀 Como Executar

### 1. Executável Pré-compilado (Recomendado)
Você pode rodar diretamente o executável compilado sem precisar instalar o Python:
- Acesse a pasta `app/dist/OpenVynilRipper/`
- Execute `OpenVynilRipper.exe`

### 2. A partir do Código-Fonte (Desenvolvimento)
Certifique-se de ter o **Python 3.10+** instalado:

```bash
# Clone o repositório
git clone https://github.com/conrider3000/OpenVynilRipper.git
cd OpenVynilRipper/app

# Instale as dependências
pip install -r requirements.txt

# Execute a aplicação
python vinyl_converter.py
```

---

## ⚙️ Requisitos de Áudio
- Conecte o toca-discos ION Audio (ou dispositivo de áudio USB compatível) antes de abrir o aplicativo.
- Certifique-se de que o **FFmpeg** está instalado e adicionado ao `PATH` do sistema para conversão em MP3/FLAC com tags completas.

---

## 📄 Licença
Distribuído sob a licença MIT.
