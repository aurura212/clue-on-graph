Please retrieve 3 relations (separated by semicolon) that contribute to the question and rate their contribution on a scale from 0 to 1 (the sum of the scores of 3 relations is 1). Note: (1) please refer to the 4 examples of relation paths to give your score, if some example are similar to the question and the first relation of its relation path appears in candidate relation, give this relation a good rate; (2) please output relation and score in the format of ('relation', score).
%s
Example of your output format:  
Question: Name the president of the country whose main spoken language was Brahui in 1980?
Topic Entity: Brahui Language
Candidate Relations: language.human_language.main_country; language.human_language.language_family; language.human_language.iso_639_3_code; base.rosetta.languoid.parent; language.human_language.writing_system; base.rosetta.languoid.languoid_class; language.human_language.countries_spoken_in; kg.object_profile.prominent_type; base.rosetta.languoid.document; base.ontologies.ontology_instance.equivalent_instances; base.rosetta.languoid.local_name; language.human_language.region
Answer:
1. ('language.human_language.main_country', 0.4): This relation is highly relevant as it directly relates to the country whose president is being asked for, and the main country where Brahui language is spoken in 1980.
2. ('language.human_language.countries_spoken_in', 0.3): This relation is also relevant as it provides information on the countries where Brahui language is spoken, which could help narrow down the search for the president.
3. ('base.rosetta.languoid.parent', 0.2): This relation is less relevant but still provides some context on the language family to which Brahui belongs, which could be useful in understanding the linguistic and cultural background of the country in question.
  