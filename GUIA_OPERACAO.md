# Guia de Operação — Sistema PICMED (ECG/VFC)

Este guia descreve o procedimento passo a passo para colocar o sistema em funcionamento, desde a conexão física do hardware até a visualização dos dados na interface web.

## 1. Conexão do Hardware
1. Conecte o sensor **AD8232** e o módulo **ADS1115** ao **ESP32** conforme o esquema elétrico do projeto.
2. Utilize um cabo **USB OTG** para conectar o ESP32 ao seu smartphone Android.
3. Certifique-se de que o LED do ESP32 acenda (indicando alimentação).

## 2. Preparação no Termux
Abra o aplicativo Termux e siga os passos abaixo:

### Passo A: Identificar o dispositivo USB
Execute o comando abaixo para listar os dispositivos conectados:
```bash
termux-usb -l
```
Anote o caminho do dispositivo (geralmente algo como `/dev/bus/usb/001/002`).

### Passo B: Solicitar Permissão USB
O Android exige permissão explícita para que o Termux acesse o USB. Execute:
```bash
termux-usb -r /dev/bus/usb/001/002
```
*(Substitua o caminho pelo que você anotou no Passo A).*
**Fique atento à tela do celular:** Uma caixa de diálogo aparecerá perguntando se deseja permitir que o Termux acesse o dispositivo USB. Clique em **OK**.

## 3. Iniciando a Ponte Serial (Bridge)
A ponte é necessária para que o código Python consiga "falar" com o USB através de um arquivo virtual (PTY).

Execute o script de inicialização utilizando o `termux-usb -e`:
```bash
termux-usb -e ./run_bridge.sh /dev/bus/usb/001/002
```
*   Este comando solicitará a permissão (se ainda não tiver) e iniciará o script passando o descritor de arquivo USB correto.
*   O comando criará um link simbólico em `~/ttyesp32`.
*   A ponte ficará rodando. **Não feche esta sessão do Termux** (ou abra uma nova aba para o servidor web).
*   Você pode verificar o status em `bridge.log`.

## 4. Iniciando o Servidor Web
Com a ponte ativa, inicie a aplicação principal:

1. Ative o ambiente virtual:
   ```bash
   source .venv/bin/activate
   ```
2. Inicie o servidor:
   ```bash
   python app.py
   ```

O servidor estará rodando em `http://localhost:5000`.

## 5. Utilizando a Interface Web
1. Abra o navegador no seu celular e acesse: **http://localhost:5000**
2. No campo "Porta Serial", verifique se está preenchido com: `/data/data/com.termux/files/home/ttyesp32`
3. Clique em **Conectar**.
4. Se o status mudar para **CONECTADO_IDLE**, o sistema está pronto.
5. Selecione um participante (ou cadastre um novo) e clique em **Iniciar Leitura**.

---

## Solução de Problemas Comuns

*   **Erro "Permission Denied" no Termux-USB:** Desconecte o cabo, conecte novamente e repita o comando `termux-usb -r`. Certifique-se de que nenhum outro app (como um explorador de arquivos) capturou o USB primeiro.
*   **ESP32 Reiniciando Sozinho:** A ponte foi modificada para desativar o DTR/RTS, o que deve resolver este problema. Se persistir, verifique a qualidade do cabo USB.
*   **Gráfico não atualiza:** Verifique se o status na interface web está como "CONECTANDO" ou "ERRO". Se estiver em "ERRO", tente desconectar e conectar novamente pela interface.
