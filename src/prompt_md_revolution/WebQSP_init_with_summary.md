Given a question, a Topic Entity in the Question and Summary of Topic Entity, output possible freebase Relation Paths starting from the Topic Entities in order to answer the question. 
Here are some RULES you must obey:
1. Summary of Topic Entity contains information about the triples including the topic entity in the knowledge graph. In the Summary of Topic Entity, the relation of triple is in (). When making Relation Paths predictions, refer to the Summary of Topic Entity.
2. Use a json dict as output format, the key of which is the Topic Entities of the Question and the value is an array of array, each inner array is a relation path from the Topic Entity to the answer of the question. You should output different Relation Paths for each Topic Entities, according to the question. The Paths are stored in an array.
3. You must output at least 2 different possible relation paths starting from this topic entity. The differences between the paths can be different relations or the number of relations. The number of relations in the path can be one or more.
4. For your information, the Freebase knowledge base stores knowledge in different structures from the natural language. In other words, a relation in natural language can be represented by several (one or two or more) relations in the knowledge base. That is why I want you to output several different possible paths.
5. Please think step by step.
#
Question: where is aviano air force base located?
Topic Entity: "aviano air force base"
Summary of Topic Entity: 
aviano air force base: Aviano Air Base is contained within Italy (location.location.containedby).
Thought: Firstly, the path should cover location containing aviano air force base.
Path: {
"aviano air force base":[
    "aviano air force base -> location.location.containing",
    "aviano air force base -> location.location.containedby",
]
}
#
Question: what major airport is near destin florida?
Topic Entity: "destin florida"
Summary of Topic Entity: 
destin florida: Destin has nearby airports (location.location.nearby_airports) such as Destin–Fort Walton Beach Airport and Destin Executive Airport.
Thought: Firstly, the path should cover airports near destin florida. Second, it should cover the number of runways to finally now the major one.
Path: {
"destin florida":[
    "destin florida -> location.location.nearby_airports -> aviation.airport.number_of_runways",
    "destin florida -> location.location.airports_near -> aviation.airport.major_airport",
]
}
#
Question: where did laura ingalls wilder live?
Topic Entity: "laura ingalls wilder"
Summary of Topic Entity: 
laura ingalls wilder: Laura Ingalls Wilder (people.person.place_of_birth) was born in Pepin and (people.deceased_person.place_of_death) died in Mansfield. She (people.person.places_lived) lived in m.0_ghkyv, m.0_ghjwl, m.0_gghtv, m.0_ghjyl, m.0_ghk_x, and m.0_gghr5. Pepin (location.location.people_born_here) is associated with her birth, and Mansfield (people.cause_of_death.people) is linked to her death. She (people.person.nationality) was a citizen of the United States of America.
Thought: Firstly, the path should cover the place where laura ingalls wilder live.
Path: {
"laura ingalls wilder":[
    "laura ingalls wilder -> people.person.places_lived -> people.place_lived.location", 
    "laura ingalls wilder -> place.place.person_lived -> location.location.place", 
]
}
#

