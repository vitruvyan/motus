# Demos

The files under `demo/out/` are frozen evidence from releases through 0.13.0;
they contain no commitment-log window files. External window evidence from
releases through 0.13.0 may retain the legacy on-disk key `c`; Motus readers
accept it and report `written by a Motus before 0.14.0`. New logs write
`commitment`. Do not rewrite the frozen output.
