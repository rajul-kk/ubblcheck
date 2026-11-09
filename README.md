# ubbl-precheck

Pre-submission checks for Malaysian fire (Bomba) submissions under the Uniform
Building By-Laws 1984, Parts VII and VIII.

Most rejected Bomba submissions fail on paperwork, not on engineering: an item
missing from the drawings, passive and active sets that disagree, or a number
that breaks a schedule limit. This tool catches those before the submission
goes in.

Status: early. The UBBL tables are in `src/ubblcheck/ubbl.py`, each with the
clause it comes from.
