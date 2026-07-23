"""Frente de IA de triagem de chamados (plano_md_mestre_IA.md).

Estrutura-alvo (Seção 2.2 do plano IA):
- ``cliente.py``  — cliente HTTP compatível-OpenAI, provedor-agnóstico (F0).
- ``triagem.py``  — motor de triagem: contexto → chamada → ação (F1).
- ``schemas.py``  — modelos Pydantic da saída estruturada por passe (F1).
- ``prompts/``    — prompts versionados como arquivos (F1+; prompt = código).
- ``contexto_quimico.py`` — recuperação seletiva da base sigilosa (F4).
"""
