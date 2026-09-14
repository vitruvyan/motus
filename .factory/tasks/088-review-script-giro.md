# Task 088 — I sei rilievi della review DeepSeek su motus #169 (stesso ramo `ci/review-script-2`)

**Ruolo:** implementatore. Perimetro chiuso: sei correzioni piccole in due file (`.github/scripts/openrouter_review.py`,
`.github/workflows/openrouter-review.yml`) e nel test `tests/tools/test_openrouter_review.py`. Worktree: quella in cui
leggi (tutto committato fino a HEAD). Scrivi solo qui. Nessun commit, push, PR, rete. **Test in primo piano, una
correzione alla volta.** Leggi SOLO i tre file sopra e il commento di review su
https://github.com/vitruvyan/motus/pull/169 (già riassunto qui sotto: non serve la rete).

1. **Dottrina annidata e contenuto della PR.** Il workflow gira su `pull_request_target` con checkout del BASE: i file
   sono letti dal base, mai dalla PR. Scrivilo nel docstring di `nested_guideline_paths`/`load_guidelines` e aggiungi
   un test che dimostra che un `AGENTS.md` presente solo nel diff (path nel diff, file assente nel `--repo-root`) NON
   entra nella dottrina. Nessuna lettura fuori da `--repo-root`.
2. **Il commento unico non degrada in silenzio.** Nel workflow, la ricerca del commento precedente (`gh api … | jq …`)
   ha `set -euo pipefail` e fallisce a voce alta se la lookup non riesce (permesso o rete), invece di creare un
   duplicato; dichiaralo nel commento del passo.
3. **Budget totale della dottrina.** Sopra il tetto per file (40k), un tetto totale `MAX_TOTAL_GUIDELINES_CHARS = 80_000`:
   oltre, i file oltre budget vengono omessi con il marcatore `[guidelines omitted: <path> (total budget)]`, mai il diff.
   Test con 5 file da 30k.
4. **Ordine per vicinanza, non alfabetico.** I file annidati sono ordinati per numero di path toccati che stanno nella
   loro directory (decrescente), poi per profondità (più profondo prima), poi alfabetico; il cap a 5 taglia dopo.
   Test: due directory, una toccata da 3 file e una da 1 → la prima viene prima; neutralizzazione (ordine alfabetico)
   → rosso.
5. **Il test del marcatore legge il workflow.** `test_the_marker_is_exactly_the_workflow_marker` apre
   `.github/workflows/openrouter-review.yml` e asserisce che il letterale del marcatore compaia lì. Neutralizzazione:
   cambia il marcatore nel workflow → rosso.
6. **`diff_paths`: nota onesta.** Docstring: il parsing per prefisso può prendere per intestazione una riga di contenuto
   che inizia con `-- a/` e non gestisce i path quotati di git; effetto: una lookup di dottrina in più, mai un errore.
   Aggiungi il newline finale ai file che ne sono privi.

Chiusura: `python3 -m pytest tests/tools -q` verde; `python3 tools/mutation_probe.py` con i probe esistenti più due
nuovi (ordine per vicinanza; budget totale) → tutti uccisi. `REPORT.md` in `.factory/tasks/088-review-script-giro/`;
chiudi con FATTO. Tempo atteso: meno di 40 minuti.
