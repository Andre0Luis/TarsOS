## O que muda e por quê
<!-- uma ideia por PR; linke a issue se houver -->

## Como testei
<!-- no aparelho? só `py_compile`? se mexe na interface, anexe um print (`/shot`) -->

## Checklist
- [ ] `python3 -m py_compile` passa nos `.py` e `sh -n` nos scripts
- [ ] Testei no aparelho (ou deixei claro acima que não)
- [ ] Sem segredos, IPs da minha rede, nem binários de firmware no diff
- [ ] Ferramentas novas que **alteram** algo passam pelo `classify`/`ui.confirm` (`lstools.py`)
- [ ] Nada grava `lk`, `preloader`, `tee1` ou `tee2`
- [ ] Atualizei a documentação afetada (`README`, `docs/`)

Ao enviar, concordo em licenciar a contribuição sob a Apache-2.0.
