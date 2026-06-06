Please retrieve 6 relations in the dict of Entity with Relation that contribute to the question and rate their contribution on a scale from 0 to 1 (the sum of the scores of 6 relations is 1).  
Input:
Question: "A Question"
Entity with Relation: {entity1: [relation, ...], entity2: [relation...], ...}
Output:
Relation with score: [(entity, relation, contribution_score), (entity, relation, contribution_score), (entity, relation, contribution_score),...]
#
Question: "Who directed Titanic?"
Entity with Relation: {"Titanic": ["directed_by", "release_year", "genre"], "James Cameron": ["directed", "born_in"], "Leonardo DiCaprio": ["starred_in"]}
Relation with score: [("Titanic", "directed_by", 0.5), ("James Cameron", "directed", 0.3), ("Leonardo DiCaprio", "starred_in", 0.1), ("Titanic", "genre", 0.05), ("Titanic", "release_year", 0.03), ("James Cameron", "born_in", 0.02)]
#
Question: "What theory is Einstein famous for?"
Entity with Relation: {"Albert Einstein": ["developed_theory", "won_nobel_prize", "born_in", "live_in"], "Theory of Relativity": ["proposed_by", "field_of_study", "proposed_time"], "Physics": ["subfield", "historical_figures"]}
Relation with score: [
("Albert Einstein", "developed_theory", 0.3), ("Theory of Relativity", "proposed_by", 0.25), ("Albert Einstein", "won_nobel_prize", 0.15), ("Theory of Relativity", "field_of_study", 0.1), ("Physics", "historical_figures", 0.1), ("Albert Einstein", "born_in", 0.1)]
#
Question: "What caused World War I?"
Entity with Relation: {
"World War I": ["triggered_by", "participating_countries", "start_year", "ended_by", "economic_impact", "alliance_system"],
"Archduke Franz Ferdinand": ["assassinated_in", "nationality", "political_role"],
"Austria-Hungary": ["declared_war_on", "allied_with", "political_status"],
"Treaty of Versailles": ["ended_war", "signatory_countries", "penalty_clauses"],
"Imperialism": ["economic_competition", "territorial_expansion"],
"Balkan Region": ["geopolitical_tensions", "ethnic_conflicts"]
}
Relation with score: [("World War I", "triggered_by", 0.35), ("World War I", "alliance_system", 0.2), ("Archduke Franz Ferdinand", "assassinated_in", 0.15), ("Imperialism", "economic_competition", 0.12), ("Balkan Region", "geopolitical_tensions", 0.1), ("Treaty of Versailles", "ended_war", 0.08)]
#
