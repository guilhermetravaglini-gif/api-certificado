from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field
import requests
from bs4 import BeautifulSoup
import re
import base64
import tempfile
import os
from typing import Optional
from cryptography.hazmat.primitives.serialization import pkcs12
from cryptography.hazmat.backends import default_backend
from cryptography.hazmat.primitives import serialization

app = FastAPI(
    title="API Extrator NFS-e com Certificado A1",
    description="API para extração de faturamento do Portal NFS-e Nacional usando Certificado Digital A1",
    version="1.0.0"
)

class FaturamentoRequestCertificado(BaseModel):
    certificado_base64: str = Field(..., description="Certificado A1 em base64")
    senha_certificado: str = Field(..., description="Senha do certificado")
    ano: str = Field(..., description="Ano (ex: 2025)", pattern=r"^\d{4}$")
    mes: Optional[str] = Field(None, description="Mês (1-12, opcional)")

class FaturamentoResponse(BaseModel):
    CNPJ: str
    Faturamento: float
    Notas_Encontradas: int
    Periodo: str
    Mes: str

def fazer_login_certificado(certificado_base64, senha_certificado):
    """Realiza login com certificado A1 e retorna sessão autenticada"""
    
    try:
        # Decodifica o base64
        cert_data = base64.b64decode(certificado_base64)
        
        # Carrega o certificado e chave privada
        private_key, certificate, ca_certs = pkcs12.load_key_and_certificates(
            cert_data,
            senha_certificado.encode(),
            backend=default_backend()
        )
        
    except Exception as e:
        raise Exception("Autenticação não realizada. Favor inserir os dados corretamente de acesso")
    
    # Cria diretório temporário
    temp_dir = tempfile.mkdtemp()
    cert_path = os.path.join(temp_dir, 'cert.pem')
    key_path = os.path.join(temp_dir, 'key.pem')
    
    # Salva certificado
    with open(cert_path, 'wb') as f:
        f.write(certificate.public_bytes(serialization.Encoding.PEM))
    
    # Salva chave privada
    with open(key_path, 'wb') as f:
        f.write(private_key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption()
        ))
    
    # Cria sessão
    session = requests.Session()
    session.headers.update({
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8'
    })
    
    # Configura certificado client
    session.cert = (cert_path, key_path)
    
    try:
        # Acessa a página de certificado
        url = "https://www.nfse.gov.br/EmissorNacional/Certificado"
        response = session.get(url, timeout=30)
        
        # Verifica se autenticou (cookie Emissor presente)
        if 'Emissor' not in session.cookies:
            raise Exception("Autenticação não realizada. Favor inserir os dados corretamente de acesso")
        
        # Extrai CNPJ do usuário
        cnpj = None
        soup = BeautifulSoup(response.text, 'html.parser')
        dropdown_perfil = soup.find('li', class_='dropdown perfil')
        if dropdown_perfil:
            texto = dropdown_perfil.get_text()
            cnpj_match = re.search(r'CNPJ:\s*(\d+)', texto)
            if cnpj_match:
                cnpj_limpo = cnpj_match.group(1)
                if len(cnpj_limpo) == 14:
                    cnpj = f"{cnpj_limpo[:2]}.{cnpj_limpo[2:5]}.{cnpj_limpo[5:8]}/{cnpj_limpo[8:12]}-{cnpj_limpo[12:]}"
        
        # Guarda os caminhos dos arquivos para limpar depois
        session.temp_cert_path = cert_path
        session.temp_key_path = key_path
        session.temp_dir = temp_dir
        
        return session, cnpj
        
    except requests.exceptions.SSLError:
        # Limpa arquivos temporários
        try:
            os.remove(cert_path)
            os.remove(key_path)
            os.rmdir(temp_dir)
        except:
            pass
        raise Exception("Autenticação não realizada. Favor inserir os dados corretamente de acesso")
    except Exception as e:
        # Limpa arquivos temporários
        try:
            os.remove(cert_path)
            os.remove(key_path)
            os.rmdir(temp_dir)
        except:
            pass
        if "Autenticação não realizada" in str(e):
            raise
        raise Exception("Autenticação não realizada. Favor inserir os dados corretamente de acesso")

def limpar_arquivos_temporarios(session):
    """Limpa arquivos temporários do certificado"""
    try:
        if hasattr(session, 'temp_cert_path'):
            os.remove(session.temp_cert_path)
        if hasattr(session, 'temp_key_path'):
            os.remove(session.temp_key_path)
        if hasattr(session, 'temp_dir'):
            os.rmdir(session.temp_dir)
    except:
        pass

def processar_pagina(soup, ano_filtro, mes_filtro):
    """Processa uma página de notas e retorna faturamento, quantidade e se deve continuar"""
    faturamento_pagina = 0.0
    notas_na_pagina = 0
    continuar = True
    
    tbody = soup.find('tbody')
    if not tbody:
        return 0.0, 0, False
    
    linhas = tbody.find_all('tr')
    if not linhas:
        return 0.0, 0, False
    
    for linha in linhas:
        try:
            img_gerada = linha.find('img', src='/EmissorNacional/img/tb-gerada.svg')
            if not img_gerada:
                continue
            
            td_competencia = linha.find('td', class_='td-competencia')
            if not td_competencia:
                continue
            
            competencia_texto = td_competencia.get_text(strip=True)
            match = re.search(r'(\d{2})/(\d{4})', competencia_texto)
            if not match:
                continue
            
            mes_nota = match.group(1)
            ano_nota = match.group(2)
            
            if int(ano_nota) < int(ano_filtro):
                continuar = False
                break
            
            if int(ano_nota) > int(ano_filtro):
                continue
            
            if mes_filtro and mes_nota != mes_filtro:
                continue
            
            td_valor = linha.find('td', class_='td-valor')
            if not td_valor:
                continue
            
            valor_texto = td_valor.get_text(strip=True)
            valor_limpo = valor_texto.replace('.', '').replace(',', '.')
            valor = float(valor_limpo)
            
            faturamento_pagina += valor
            notas_na_pagina += 1
        except:
            continue
    
    return faturamento_pagina, notas_na_pagina, continuar

def buscar_notas(session, ano, mes):
    """Busca e processa todas as notas fiscais"""
    faturamento_total = 0.0
    notas_processadas = 0
    pagina = 1
    continuar = True
    url_base = "https://www.nfse.gov.br/EmissorNacional/Notas/Emitidas"
    
    while continuar:
        url = url_base if pagina == 1 else f"{url_base}?pg={pagina}"
        response = session.get(url, timeout=30)
        if response.status_code != 200:
            break
        
        soup = BeautifulSoup(response.text, 'html.parser')
        faturamento_pagina, notas_pagina, continuar = processar_pagina(soup, ano, mes)
        
        faturamento_total += faturamento_pagina
        notas_processadas += notas_pagina
        
        if not continuar:
            break
        
        paginacao = soup.find('div', class_='paginacao')
        if not paginacao:
            break
        
        link_proxima = paginacao.find('a', title='Próxima')
        if not link_proxima or 'javascript:' in link_proxima.get('href', ''):
            break
        
        pagina += 1
    
    return faturamento_total, notas_processadas

@app.get("/")
def read_root():
    return {
        "status": "ok", 
        "message": "API Extrator NFS-e com Certificado A1 online",
        "docs": "/docs"
    }

@app.post("/api/faturamento-certificado", response_model=FaturamentoResponse)
def obter_faturamento_certificado(request: FaturamentoRequestCertificado):
    """
    Extrai o faturamento de NFS-e do Portal Nacional usando Certificado Digital A1
    
    - **certificado_base64**: Arquivo .pfx ou .p12 convertido em base64
    - **senha_certificado**: Senha do certificado digital
    - **ano**: Ano da consulta (formato YYYY)
    - **mes**: Mês da consulta (1-12, opcional - se não informado, retorna o ano todo)
    """
    session = None
    
    try:
        # Valida e formata o mês
        mes_filtro = None
        if request.mes:
            mes_int = int(request.mes)
            if mes_int < 1 or mes_int > 12:
                raise HTTPException(status_code=400, detail="Mês inválido")
            mes_filtro = str(mes_int).zfill(2)
        
        periodo = f"{mes_filtro}/{request.ano}" if mes_filtro else request.ano
        mes_label = mes_filtro if mes_filtro else "Ano todo"
        
        # Faz login com certificado
        session, cnpj = fazer_login_certificado(
            request.certificado_base64,
            request.senha_certificado
        )
        
        if not cnpj:
            cnpj = "Não identificado"
        
        # Busca as notas
        faturamento, quantidade = buscar_notas(session, request.ano, mes_filtro)
        
        # Limpa arquivos temporários
        limpar_arquivos_temporarios(session)
        
        return FaturamentoResponse(
            CNPJ=cnpj,
            Faturamento=round(faturamento, 2),
            Notas_Encontradas=quantidade,
            Periodo=periodo,
            Mes=mes_label
        )
        
    except Exception as e:
        # Limpa arquivos temporários em caso de erro
        if session:
            limpar_arquivos_temporarios(session)
        
        if "Autenticação não realizada" in str(e):
            raise HTTPException(status_code=401, detail=str(e))
        raise HTTPException(status_code=500, detail=f"Erro: {str(e)}")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
