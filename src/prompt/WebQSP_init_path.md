You are tasked with generating relation paths to help search for answers in Freebase based on a given question. I will provide you with:\n
1. A question.\n
2. One or more topic entity that is central to the question.\n
3. A set of valuable relations with linked entities associated with the topic entity.\n

Your goal is to generate relation paths that start with the topic entity and follow a sequence of relations to help answer the question. \n
#
Input Format:\n
Question: #A question#
Topic Entities: topic_entity1, topic_entity2
Valuable Relations: {"topic_entity1": ['relation([linked_entity])','relation([linked_entity])',...],"topic_entity2": ['relation([linked_entity])', 'relation([linked_entity])',...],...}

Output format:\n
Thought: #Your thinking process#
Path: {"topic_entity1": ["topic_entity1 -> path1_relation1 -> ..."], "topic_entity2": ["topic_entity2 -> path2_relation1 -> ..."]}
#
Rules for generating the relation paths:\n
1. Use a json dict as output format, the key of which is the Topic Entities of the Question and the value is an array of array, each inner array is a relation path from the Topic Entity to the answer of the question. You should output different Relation Paths for each Topic Entities, according to the question. The Paths are stored in an array.
2. Each relation path starting from the topic entity must use a relation in valuable relations of corresponding topic entity as the relation1 of relation path. Note: The contents in parentheses are entities linked to the relation. You can refer to the entities in parentheses to generate relation path, but do not generate an relation path with the contents of the parentheses\n
3. Each question might have one or more topic entities, and each relation path might have one or more relations. Please generate relation path for each topic entity and each topic entity can have at most two relation paths.\n


Each relation should be formatted as: domain.type.property, where:\n
1. domain.type represents the category of the entity.\n
2. property represents the actual meaning of the relation.\n
Now, based on the question, topic entity, and valuable relations I provide, generate relation path to help search for the answer in Freebase. Do not give me other things:
#
Question: where is aviano air force base located?
Topic Entity: "aviano air force base"
Valuable Relations: {"aviano air force base":['aviation.airport.icao', 'aviation.airport.serves', 'location.location.geolocation', 'location.location.containedby', 'location.location.containing']}
Thought: Firstly, the path should cover location containing aviano air force base.
Path: {
"aviano air force base":[
    "aviano air force base -> location.location.containing",
    "aviano air force base -> location.location.containedby"
]
}
#
Question: what major airport is near destin florida?
Topic Entity: "destin florida"
Valuable Relations: {"destin florida":['location.location.nearby_airports', 'location.location.containedby', 'location.location.airports_near']}
Thought: Firstly, the path should cover airports near destin florida. Second, it should cover the number of runways to finally now the major one.
Path: {
"destin florida":[
    "destin florida -> location.location.nearby_airports -> aviation.airport.number_of_runways",
    "destin florida -> location.location.airports_near -> aviation.airport.major_airport"
]
}
#
Question: who played princess leia in star wars movies?
Topic Entity: princess leia
Valuable Relations: {"princess leia":['film.film_character.portrayed_in_films', 'tv.tv_character.appeared_in_tv_program', 'film.film_character.movie', 'movie.movie_character.movie']}
Thought: Firstly, the path should cover the movies portrying princess leia. Secondly, the path should cover the actors in that movie.
Path: {
    "princess leia":[
        "princess leia -> film.film_character.portrayed_in_films -> film.performance.actor", 
        "princess leia -> movie.movie_character.movie -> film.actor.actor" 
]
}
#
