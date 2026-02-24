ssh -i "C:\\Users\\anton\\.ssh\\trading-server.key" ubuntu@137.131.244.25

cd ~/AgenteTradeQuotex

source venv/bin/activate

python3.13 main.py


**Iniciar em background (screen):**


screen -S trading

python3.13 main.py


**Sair sem matar: Ctrl+A + D**

**Retomar** **session**: screen -r trading



**Tudo de uma vez:**

cd ~/AgenteTradeQuotex \&\& source venv/bin/activate \&\& python3.13 main.py

