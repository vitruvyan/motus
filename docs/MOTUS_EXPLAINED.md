# Motus, spiegato da zero

Questo documento non presume niente: né crittografia, né blockchain, né Motus.
È il racconto del problema, dell'idea, e di cosa abbiamo costruito — nell'ordine
in cui le cose sono state capite, errori compresi.

*Ultimo aggiornamento: 13 agosto 2026 — fase 1 della roadmap, ciclo di vita
appena agganciato al runtime, round avversariale in corso.*

---

## Il problema

Un'azienda usa l'intelligenza artificiale per decidere qualcosa che conta: se
concedere un mutuo, se un'analisi di sicurezza può uscire dall'ufficio. Poi
qualcuno chiede conto di una di quelle decisioni — un revisore, un cliente, un
giudice.

L'azienda apre i suoi log e dice: *«ecco, è andata così»*.

Il problema è tutto lì: **come fa chi guarda a sapere che quel file non è stato
modificato dopo?** I log sono file di testo. Chi li possiede li può riscrivere.
Non serve nemmeno malafede — basta un bug, un backup ripristinato male, un
collega che "sistema" una riga.

Motus esiste per rispondere a quella domanda.

## Il primo pezzo: la catena

L'idea di base è vecchia e semplice. Ogni passo della decisione viene
registrato, e ogni registrazione porta con sé **l'impronta digitale della
registrazione precedente**.

Un'impronta digitale — un *hash* — è un numero che si calcola dal contenuto.
Cambia una virgola e il numero viene completamente diverso. Non si può andare
all'indietro: dall'impronta non si risale al testo.

Cento registrazioni concatenate così: se ne modifichi una, la sua impronta
cambia; ma quell'impronta era scritta dentro la successiva, che cambia anche
lei; e così via fino in fondo. **Toccare una riga in mezzo rompe tutto quello
che viene dopo.**

Il valore finale — l'impronta dell'ultima registrazione, che dipende da tutte
le altre — si chiama **root**. Una stringa di settantuno caratteri che riassume
l'intera decisione.

## Il difetto che abbiamo trovato, e perché è istruttivo

Quando calcolavamo l'impronta di una registrazione, **azzeravamo il campo che
conteneva l'impronta della precedente**. Sembrava innocuo. Non lo era:
significava che nessuna impronta copriva davvero il collegamento con quella
prima. La catena c'era in apparenza, ma i suoi anelli non erano attaccati.

Conseguenza pratica: chi conosceva le nostre regole — che sono pubbliche,
apposta — poteva ribaltare un verdetto da APPROVATO a RESPINTO, ricalcolare
tutte le impronte, e **la root restava identica**. Il valore che pubblicavamo
come riassunto della decisione era d'accordo con la decisione falsificata.

Peggio: con l'orologio fissato — cosa che facciamo per rendere i test
ripetibili — **tre run completamente diverse producevano la stessa root**.

Corretto. E poi corretto di nuovo, perché la prima correzione ne aveva
riaperto una versione più sottile un livello più su.

## Il secondo pezzo: l'ancora

Anche con la catena perfetta resta un buco che nessuna matematica chiude: **chi
possiede il file può rifare tutto da capo.** Riscrive la decisione, ricalcola
l'intera catena, ricalcola la root. Tutto coerente. Nessuno se ne accorge.

L'unico rimedio è pubblicare la root **dove nemmeno tu puoi tornare a
cambiarla**. L'abbiamo messa dentro una transazione su una blockchain pubblica —
una di quelle in cui riscrivere la storia costerebbe una fortuna.

Il 12 agosto 2026 l'abbiamo fatto davvero:

```
txid   6010ded80e15b8005a14c5f13ef44aa36a11d9f0cde75c961ef5e11a49ea17b0
rete   TRON Nile (rete pubblica di test)
memo   VITRUVYAN_AUDIT:sha256:cb6829d3…b2468a92
```

Chiunque può aprirla e leggere la nostra root lì dentro. Se domani
modificassimo il file, il ricalcolo darebbe un numero diverso da quello sulla
catena pubblica, e la bugia si vedrebbe.

**Una distinzione che quasi tutti sbagliano:** questo non rende il file
immodificabile. Un file è sempre modificabile. Toglie la possibilità di **far
credere** alla modifica. Sono cose diverse, e confonderle è il genere di frase
che un compratore tecnico smonta in riunione.

## Il terzo pezzo: la decisione che non viene registrata

Fin qui abbiamo protetto le decisioni **che vengono registrate**. Ma cosa
impedisce di non registrarne una?

L'azienda fa girare la decisione, guarda com'è andata, e se il risultato è
imbarazzante non lo scrive da nessuna parte. Tutte le altre restano perfette e
verificabili. Quella no.

### Due idee sbagliate, in ordine

**Prima idea: contare.** Se ci sono le ricevute 40 e 42, la 41 è stata
soppressa. Falso: se il numero viene assegnato quando decidi di registrare, la
run scomoda non prende nessun numero e la successiva prende il 41. Nessun buco.

**Seconda idea: scrivere spesso**, così la finestra per barare è stretta. Falso
anche questo, e peggio, perché è sopravvissuto alla prima correzione.
L'operatore sa com'è andata **quando la run finisce** — non gli serve prevedere
niente.

### La risposta: due tempi

```
BEGIN   ->   la run gira   ->   END
```

Il `BEGIN` si scrive **prima** che la decisione venga presa, quando ancora
nessuno sa come andrà. L'`END` dopo, e porta la root dell'evidenza prodotta.

Una run soppressa lascia un `BEGIN` senza `END`: si è cominciato qualcosa e non
risulta nessuna conclusione. Non è una prova di colpa — un processo può morire,
una macchina si può spegnere — ma è **una domanda che prima non esisteva**.

**Quanto spesso capita innocentemente?** È la domanda che regge tutto: se un
`BEGIN` spaiato è comune, non significa più niente. Nel documento di
architettura avevamo scritto che il caso innocente è *«un processo ucciso tra le
due scritture»*, e l'abbiamo tenuto per buono finché un attacco non ha prodotto
un `BEGIN` spaiato **senza nessun crash**: basta smettere di leggere uno stream
a metà, cosa che Motus permette apposta.

Il meccanismo era giusto — in tutti e due i casi l'evidenza è incompleta
esattamente come l'impegno, quindi i due documenti concordano. Sbagliata era
l'*enumerazione*: avevamo descritto una classe nominando l'unico membro che ci
era venuto in mente. E questo cambia una cosa sola, che però è quella portante:
letto come *«processo ucciso»*, sembra che il caso sia raro **perché il software
è affidabile**. Non è così. Il secondo membro dipende da quanto spesso si
abbandona uno stream — che è una questione di *come si usa* il sistema, non di
quanto è stabile. Va misurato dove gira, non dedotto da un documento.

La lezione, per chi comincia: **quando verifichi un'affermazione su una classe
di casi, il meccanismo non basta.** Noi avevamo controllato *«funziona per
questo caso?»* — sì, sempre — e mai *«quali sono tutti i casi?»*. La prima
domanda dà una risposta rassicurante e la seconda è quella che trova l'errore.

E c'è una condizione che abbiamo dovuto imparare a dichiarare: non basta che il
`BEGIN` venga *prima nel tempo*. Deve essere **uscito dalle mani di chi lo
scrive** prima che l'esito sia noto, altrimenti lo si cancella con calma dopo.
La proprietà non è temporale, è **di custodia** — e questa distinzione ci è
sfuggita tre volte di fila, sempre nello stesso modo: attribuire alla matematica
una garanzia che vale solo sotto una condizione che nessuno aveva scritto.

## Cosa esiste, in pratica

**Un magazzino su disco** che tiene questi impegni. Ha una regola centrale che
sembra banale e non lo è: *scrivi sul disco, aspetta la conferma del sistema
operativo, e solo dopo dillo alla memoria del programma.* La prima versione
faceva il contrario, e una scrittura fallita lasciava in memoria un impegno che
sul disco non c'era mai stato — con il risultato che tutte le prove successive
erano sbagliate senza che niente si lamentasse.

**L'aggancio al motore**: il `BEGIN` prima del primo nodo, l'`END` su ogni via
d'uscita — successo, fallimento, annullamento. Se il `BEGIN` non si riesce a
scrivere, **la run non parte proprio**. Sembra drastico, ed è il punto: una run
il cui impegno può svanire non ha niente da dimostrare.

**E niente di tutto questo si accende da solo.** Se non lo configuri, Motus si
comporta esattamente come prima, bit per bit — c'è un test che esegue una run
non configurata in un processo separato e verifica che nessuno di questi moduli
sia stato nemmeno caricato.

## Perché ogni cosa viene attaccata prima di essere spedita

In questo progetto vale una regola più di qualunque test: **ogni round di
attacco ha trovato un difetto nella correzione del round precedente.** Sempre.

Sul magazzino, tre agenti hanno trovato dodici difetti, cinque gravi. Uno vale
la pena raccontarlo: avevamo ragionato correttamente che la firma del testimone
**non deve entrare nel calcolo dell'impronta** — vero — senza accorgerci che
questo non implica affatto che non vada **conservata**. Due decisioni
indipendenti, una sola presa. Risultato: la firma spariva nel momento in cui si
scriveva su disco, e la proprietà per cui esistono i due tempi diventava
indimostrabile.

Nessuno di quei dodici era arrivato a toccare una run vera, perché il magazzino
è stato costruito **prima** di attaccarlo al motore — invertendo l'ordine
scritto nella roadmap. È l'unica ragione per cui oggi sono aneddoti invece che
incidenti.

**Poi abbiamo attaccato l'aggancio al motore, e sono arrivati altri due round.**

Il primo ha trovato che un `BEGIN` che non si riesce a scrivere costava **tutto
il Runtime** e non solo quella run: il codice dell'impegno stava fuori dal
blocco che rilascia il lucchetto del ciclo di vita, quindi il lucchetto restava
chiuso e ogni run successiva veniva rifiutata. Abbiamo corretto, e il round
dopo **ha trovato la stessa identica forma dall'altra parte**, sull'`END`. Due
volte lo stesso errore, in due punti diversi, a distanza di un giorno.

Sempre nel secondo round: la scadenza oltre la quale si smette di aspettare il
testimone **esisteva solo nel commento** — non c'era nessun timeout nel codice —
e un testimone che richiamava il magazzino da cui era stato invocato bloccava
l'intero processo. Adesso la scadenza **non ha un valore di default**: se
configuri un testimone la devi scrivere tu, perché un numero scelto da noi
sarebbe un numero scelto senza sapere niente del tuo sistema.

Un difetto, però, l'ha trovato **un mio test e non un agente**, ed è il più
sottile di tutti. Per decidere se una run stava già fallendo, il codice
chiedeva a Python *«c'è un'eccezione in corso?»* — ma lo chiedeva da dentro il
blocco che gestisce le eccezioni, dove la risposta è **sempre sì**. Il
controllo era scritto, sembrava giusto, e non poteva scattare mai. È il genere
di errore che nessuna rilettura trova e che solo un test che lo *esercita* può
scoprire.

E infine: il magazzino accettava un oggetto finto. Nei test si usano oggetti
segnaposto che dicono sì a tutto; il codice non verificava che quello che
tornava fosse davvero un impegno, quindi una run poteva risultare registrata
senza che nulla fosse stato scritto. Adesso lo verifica.

## Cosa Motus non promette

Vale quanto tutto il resto, ed è scritto nel contratto:

- **non** che una decisione sia giusta;
- **non** che i fatti registrati siano veri;
- **non** che tutte le decisioni dell'organizzazione siano passate di qui;
- **non** che una run mai registrata non sia mai esistita;
- **non** la conformità a una norma. Motus produce l'evidenza *con cui* la
  conformità si dimostra; la dimostrazione è di qualcun altro.

Un sistema che non sa dire i propri limiti non ha stabilito niente.
