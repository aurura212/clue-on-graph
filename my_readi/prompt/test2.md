You are tasked with generating reasoning paths to help search for answers in Freebase based on a given question. I will provide you with:
1. A question.
2. One or more topic entity that is central to the question.
3. A set of domain.type prefixes associated with the topic entity.

Your goal is to generate reasoning paths that start with the topic entity and follow a sequence of relations to help answer the question. 
#
Input Format:
Question: #A question#\n
Topic Entities: topic_entity1, topic_entity2\n
Prefixes: {"topic_entity1": [#prefixes of topic_entity1#],"topic_entity2": [#prefixes of topic_entity2#]}\n

Output format(Json format):
Reasoning Path:
{"topic_entity1": ["topic_entity1 -> path1_relation1 -> path1_relation2 -> path1_relation3 -> ...", ...], "topic_entity2": ["topic_entity2 -> path2_relation1 -> path2_relation2 -> path2_relation3 -> ...", ...]}
#
Rules for generating the reasoning paths:
1. The first relation (relation1) of each path must use one of the provided domain.type prefixes associated with the topic entity.
2. Subsequent relations (relation2, relation3, ...) can use any domain.type prefix, not necessarily related to the topic entity.
3. Each question might have one or more topic entities, and each reasoning path might have one or more relations. Please generate reasoning path for each topic entity and each topic entity can have at most two reasoning paths.

Each relation should be formatted as: domain.type.property, where:
1. domain.type represents the category of the entity.
2. property represents the actual meaning of the relation.
#
Example:
Question: who played princess leia in star wars movies?\n
Topic Entity: princess leia\n
Prefixes: {"princess leia":  ["film.film_character", "movie.movie_character", "film.performance"]}\n
Path: {"princess leia":["princess leia -> film.film_character.portrayed_in_films -> film.performance.actor", "princess leia -> movie.movie_character.movie -> film.actor.actor", ]}
#
Now, based on the question, topic entity, and domain.type prefixes I provide, generate reasoning paths to help search for the answer in Freebase. Do not give me other things:

Question: what did james k polk do before he was president?\n
Topic Entities: james k polk\n
Prefixes: {"james k polk": ["government.politician", "government.u_s_congressperson", "government.political_party_tenure"]}
Reasoning Path: