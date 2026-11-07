# Como contribuir com o TarsOS

> **In English:** contributions are very welcome (code, docs, tests on other devices, translations). Read the safety rules below first
> (never write `lk`/`preloader`/`tee1`/`tee2`; never commit secrets or firmware blobs). You can develop most of the UI and the agent
> without the device: `py_compile`, the `lsweb.py` CLI and the screenshot endpoint. Issues and PRs may be in Portuguese or English.

Quem tem um Echo Show 5, quem só lê código, quem escreve documentação, quem testa em outro aparelho, quem traduz: tem lugar aqui.
O projeto nasceu como um hack de uma pessoa, então ainda é pequeno e informal. **Perguntas são contribuição.**

## Regras de ouro (leia antes de tudo)

1. 🧱 **Nunca grave as partições `lk`, `preloader`, `tee1` ou `tee2`** (nem em scripts, nem em exemplos, nem "só para testar"). É brick permanente. PRs que toquem nisso serão recusados.
2. 🔑 **Nada de segredos no repositório:** chaves de API, tokens, senhas, `tuya.json`, `devices.json`, chaves SSH, IDs de aparelhos, endereços da sua rede. O `.gitignore` já cobre `keys/`, `*.img` e arquivos de segredos; confira o seu `git diff` antes de cada commit.
3. 📦 **Nada de binários de firmware:** imagens `boot.img`, partições copiadas do aparelho, blobs da Amazon/MediaTek, o exploit do amonet. O projeto distribui **código nosso** e documentação; o resto o usuário obtém das fontes originais.
4. 🛡️ **Segurança do TARS é recurso, não detalhe:** qualquer ferramenta nova que **altere algo** (sistema, rede, arquivos, aparelhos) passa pelo `classify`/`ui.confirm` do `lstools.py`. O que as ferramentas devolvem é dado **não confiável**.
5. 🙂 Respeite o [Código de Conduta](CODE_OF_CONDUCT.md).

## Formas de ajudar

- **Testar e reportar:** rodou no seu Echo? Algo diferente? Use o modelo "Relato de aparelho".
- **Documentação:** corrigir o [INSTALL](docs/INSTALL.md), traduzir para o inglês, melhorar as explicações de hardware.
- **Código:** o [roadmap](README.md#roadmap) e as issues marcadas `good first issue` / `help wanted`.
- **Outros aparelhos:** a base de software pode servir ao Echo Show 5 de 2ª geração (`cronos`) e a outros Echo/Fire com Linux. Documente o que descobrir em `docs/HARDWARE.md` ou abra um PR com um novo capítulo.
- **Personalidade:** frases, estilos e traduções do TARS (veja [docs/TARS.md](docs/TARS.md#personalizando-o-tars)).

## Montando o ambiente

Você **não precisa do aparelho** para boa parte do trabalho:

```bash
git clone https://github.com/Andre0Luis/TarsOS.git && cd TarsOS
python3 -m venv .venv && . .venv/bin/activate && pip install pillow numpy pyserial   # só para testes locais

python3 -m py_compile rootfs-overlay/usr/local/lib/luizinhoshowos/*.py      # sintaxe
python3 rootfs-overlay/usr/local/lib/luizinhoshowos/lsweb.py "receita de pão de queijo"   # navegador/RSS pelo terminal
python3 rootfs-overlay/usr/local/lib/luizinhoshowos/lsweb.py rss:https://tecnoblog.net/feed/
python3 tools/bootimg_cmdline.py show boot.img                                # ferramenta de boot
```

Com o aparelho (já instalado conforme o [INSTALL](docs/INSTALL.md)):

```bash
export TARSOS_HOST=<IP-do-Echo>
tools/deploy.sh                    # sintaxe + envia código e serviços + reinicia o lsdesk (≈10 s)
tools/deploy.sh --audio            # também reinicia o lsaudiod (corta o som por um instante)
tools/deploy.sh --all              # o overlay inteiro
```

### Testando a interface sem tocar no aparelho

O `lsdesk` aceita comandos num FIFO e serve um JPEG da tela. Dá para "usar" o Echo de longe e **tirar prints** para o PR:

```bash
ssh root@$TARSOS_HOST 'echo "open noticias" > /run/lsdesk.ctl'     # abre um app (tap X Y, text abc, home, idle, say ...)
curl -s http://$TARSOS_HOST:8080/shot -o tela.jpg                  # captura (as coordenadas de tap são as da captura, 960×480)
```

Se o aparelho estiver em modo ocioso, o primeiro `tap` só acorda a tela.

### Testando o TARS fora da tela

```python
# no Echo, em /usr/local/lib/luizinhoshowos
import lsbrain, lstools, threading
class UI:                                   # um "UI" de mentira: nega confirmações
    def confirm(self, t): return False
    def event(self, t): print('[evento]', t)
    def open_browser(self, d): return {'ok': True}
    def spotify(self, c): return {}
    def set_volume(self, p): return {}
b = lsbrain.Brain(); b.tools = lstools.Tools(UI())
ev = threading.Event(); out = {}
b.ask('que horas são?', '', lambda r, e: (out.update(r=r, e=e), ev.set())); ev.wait(60); print(out)
```

## Estilo de código

- **Python primeiro, biblioteca padrão primeiro.** O aparelho tem 1 GB de RAM e `apk` como gerenciador. Dependências novas precisam de pacote `apk` ou de um bom motivo.
- **Siga o código ao redor:** comentários em português do Brasil (ou inglês, se preferir), nomes curtos, sem enfeite. Comente o **porquê** (principalmente quirks de hardware), não o óbvio.
- **Sem emojis na interface e nas falas do TARS** (o texto é lido em voz alta).
- **A tela é por toque:** não há gestos de arrastar; use botões (▲▼) para rolagem. Alvos de toque com pelo menos ~44 px.
- **Cuidado com o áudio:** nunca escreva direto no PCM do alto-falante; passe pelo `lsaudiod`/`lsaudio-play` (escritas em múltiplos de 64 bytes, ou o kernel entra em panic).
- **Scripts de shell:** POSIX `sh` quando possível; `set -e`; sem `curl | sh`.
- **Nada de rede no caminho quente da interface:** use threads e atualize com `self.dirty = self.desk.dirty = True`.

## Fluxo de contribuição

1. **Abra uma issue** (ou comente numa existente) para ideias grandes, para combinarmos a direção antes de você investir tempo.
2. *Fork* → branch curta (`feat/...`, `fix/...`, `docs/...`).
3. Faça mudanças pequenas e focadas, **uma ideia por PR**.
4. **Confira antes de enviar:**
   - [ ] `python3 -m py_compile` passa nos `.py` e `sh -n` nos scripts;
   - [ ] testei no aparelho (ou disse claramente que não);
   - [ ] se mexe na interface, **anexei um print** (`/shot`);
   - [ ] sem segredos, IPs da minha rede, nem binários no diff;
   - [ ] ferramentas novas que alteram algo passam pelo `classify`/`confirm`;
   - [ ] atualizei a documentação afetada (`README`, `docs/`).
5. **Commits** no imperativo, de preferência no formato `tipo(escopo): resumo` (`feat(tars): ...`, `fix(audio): ...`, `docs(install): ...`).
6. Abra o PR usando o modelo. Revisão é conversa: o objetivo é o projeto ficar bom, não ganhar discussão.

## Relatando bugs

Use o modelo de issue. Ajuda muito: versão do TarsOS (`showos`), o trecho de `/var/log/lsdesk.log`, o que você fez, o que esperava e o que aconteceu, e **um print** (`/shot`). Para problemas de boot, informe o tamanho do cmdline (`tools/bootimg_cmdline.py show`).

**Não cole chaves, tokens nem o conteúdo de `/etc/luizinhoshowos/`.** Vulnerabilidades: veja o [SECURITY.md](SECURITY.md).

## Licença das contribuições

O projeto é [Apache-2.0](LICENSE). Ao enviar um PR você concorda em licenciar a sua contribuição sob os mesmos termos.

**Arquivos novos** de código (`.py`, `.sh`, serviços OpenRC, `Dockerfile`) levam este cabeçalho logo depois do `#!` (se houver):

```
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The TarsOS Authors
```

Se você traz código ou dados de **outro projeto**, diga de onde veio e qual é a licença no PR, e acrescente a linha no [NOTICE](NOTICE). Não traga nada com licença incompatível com a Apache-2.0 (por exemplo, GPL copiada para dentro dos nossos arquivos).

---

*Every second counts.* Obrigado por gastar alguns dos seus aqui.
