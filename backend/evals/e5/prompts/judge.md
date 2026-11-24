# Task

You check literature passages for a prenatal lookup assistant. For each numbered passage, decide whether it supports the statement: **"Patients (or fetuses) with the disease named below have the phenotype named below."**

# Rules

Answer `true` when the passage (a paragraph, a table row, or a figure caption) says that people or fetuses affected by **that very disease** have the phenotype, or a more specific form of it.

Answer `false` when:
- the phenotype is said to be absent, or the passage does not mention it;
- the phenotype belongs to another disease or entity: another syndrome, a contiguous gene deletion, a differential diagnosis, a different gene's disorder;
- the phenotype appears only under treatment, management, surveillance or follow-up (for example "evaluate for hearing loss annually");
- the phenotype is only an item of a scoring scale, a diagnostic criterion list used for another purpose, or a test to perform (for example "echocardiogram to look for septal defects");
- the passage names only a nearby sign that the phenotype's definition does not cover (for example a "broad forehead" is not "prominent forehead"; generic "septal defects" is not "atrial septal defect");
- the statement rests on an animal model only (mice, zebrafish), not on patients.

Judge only from the words of the passage. Do not use outside knowledge.

# Output

Return `judgements`: one entry per passage, `index` = the passage number, `supports` = true or false.
