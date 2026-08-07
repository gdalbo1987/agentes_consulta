"""Regras de estilo compartilhadas pelos prompts dos agentes de IA.

Existe um único lugar para elas porque valem para TODO agente cujo texto livre
chega à tela: o de pesquisa (justificativa do match, resumo de notícia), o de
priorização (justificativa por critério), o de approach (dicas) e o de insights
(resposta do chat). Repetir a frase em cada arquivo garantiria que uma
mudança futura fosse aplicada em três dos quatro.
"""

# Pedido do usuário: nenhum travessão no texto que aparece na interface.
# Precisa ser instrução de saída do agente, não só um replace nas strings da UI:
# boa parte do texto exibido é gerada pelo modelo em tempo de execução, e o
# modelo tende a usar travessão em português a menos que seja proibido.
REGRA_SEM_TRAVESSAO = (
    "ESTILO: nunca use travessão (o caractere —) nem meia-risca (–) no "
    "texto que escrever. Onde usaria um, use vírgula, dois-pontos, ponto e "
    "vírgula ou ponto final. Hífen comum em palavra composta continua permitido."
)
