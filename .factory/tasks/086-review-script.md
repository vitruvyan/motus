# Task 086 — La review OpenRouter: test, dottrina completa, troncamento onesto, un solo commento (motus, poi orbis)

**Ruolo:** implementatore. Forma decisa qui (rilievi 2-5 della review DeepSeek su motus #168). Worktree: quella in cui
leggi (repo `vitruvyan/motus`, ramo `ci/review-script-2`, da `main` dopo la fusione di #168). Scrivi solo qui. Nessun
commit, push, PR, rete, nessuna chiamata reale a OpenRouter. **Suite in primo piano.** Leggi `.github/workflows/openrouter-review.yml`
e `.github/scripts/openrouter_review.py`; la dottrina del repo in `AGENTS.md` (ogni fix porta un test che fallisce senza).

## Le correzioni

**C1 — test dello script** (`tests/tools/test_openrouter_review.py`, stdlib + pytest, client HTTP finto): `build_payload`
(modello, messaggi, dottrina), i due troncamenti con i marcatori, il diff vuoto (nessuna chiamata, testo dichiarato),
la risposta malformata (errore leggibile, exit ≠ 0), la risposta buona → file scritto con il footer del modello.
Neutralizzazione: togli il marcatore di troncamento → rosso.

**C2 — la dottrina è la classe, non due file.** `GUIDELINE_FILENAMES` → elenco ordinato: `AGENTS.md`, `CLAUDE.md`,
`.github/copilot-instructions.md`, `.pi/AGENTS.md`, più ogni `AGENTS.md` annidato nelle directory toccate dal diff
(derivate dai path del diff, al massimo 5); ogni file entra con un'intestazione `## <path>`. Test con un albero finto.

**C3 — troncamento della dottrina onesto.** `MAX_GUIDELINES_CHARS` = 40 000; oltre, si tiene testa e coda (le
«Rules no agent may break» stanno in coda) con il marcatore `[guidelines truncated: N chars omitted in the middle]`.
Test: un file lungo → testa e coda presenti, marcatore presente.

**C4 — un solo commento per PR.** Il workflow cerca un commento precedente con il marcatore
`<!-- openrouter-review -->` e lo AGGIORNA (`gh api PATCH`) invece di accumularne uno per push; se non c'è, lo crea
(`gh pr comment`). Il testo porta il commit rivisto (`Reviewed commit: <sha>`). Test dello script per il marcatore;
il comportamento del workflow è dichiarato nel commento del file.

## Regole

Nessuna dipendenza nuova (stdlib). `python -m pytest tests/tools -q` verde; catena del repo se esiste un `make test`.
`REPORT.md` in `.factory/tasks/086-review-script/`; chiudi con FATTO. Dopo la fusione in motus, il lead porta lo
stesso script in orbis.
