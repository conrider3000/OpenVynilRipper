# Open Vynil Ripper (MVP)

Um aplicativo moderno feito em Python para capturar áudio de toca-discos USB (como os da ION Audio) e monitorar em tempo real em qualquer dispositivo de saída de áudio escolhido.

Este aplicativo resolve a limitação do software original (EZ Vinyl) de não permitir o roteamento do áudio de reprodução (monitoring) durante a gravação.

## Funcionalidades
- Interface inspirada em Apple HIG (Modo Escuro)
- Seleção visual de Entrada e Saída de áudio
- Monitoramento em tempo real sem latência perceptível
- Waveform visual e Medidores VU Estéreo em tempo real
- Gravação nativa para formato WAV
- Conversão integrada para MP3 (via `ffmpeg`)

## Como usar

1. Conecte o seu toca-discos ION Audio (ou toca-fitas) via USB no computador.
2. Certifique-se de que os requisitos estejam instalados:
   ```cmd
   python -m pip install -r requirements.txt
   ```
3. Execute o aplicativo:
   ```cmd
   python vinyl_converter.py
   ```
4. Na tela inicial, certifique-se de que a **ENTRADA** esteja definida como o seu "Dispositivo de áudio USB".
5. Defina a **SAÍDA** para a sua saída P2 desejada (ex: "Synaptics SmartAudio HD" ou "Realtek").
6. Posicione a agulha no vinil e você deverá ouvir o som passando pelas caixas/fones na mesma hora.
7. Clique em **REC** para começar a gravar.
8. Quando terminar, clique em **STOP**. O aplicativo perguntará se você quer salvar também uma cópia MP3.

> **Dica**: Certifique-se de que você tem o `ffmpeg` nas variáveis de ambiente do Windows para que a conversão MP3 funcione perfeitamente.
