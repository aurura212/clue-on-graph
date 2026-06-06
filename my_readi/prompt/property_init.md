Given a question and a Topic Entity in the Question, output possible Reasoning Process starting from the Topic Entities in order to answer the question. 
Here are some RULES you must obey:
1. Use a json dict as output format, the key of which is the Topic Entities of the Question and the value is an array of array, each inner array is a Property Chain from the Topic Entity to the answer of the question. You should output different Property Chains for each Topic Entities, according to the question. The Property Chains are stored in an array.
2. You must output at least 2 different possible Property Chains starting from this topic entity. The differences between the Chains can be different Properties or the number of Properties.
3. We use property to describe the entity on the chain. Property describes the relationship between entities and their parents entities in the knowledge graph.
4. Please think step by step. You should only give your Thought (no more than 64 tokens) and Chain.
#
Question: where is aviano air force base located?
Topic Entity: "aviano air force base"
Thought: Firstly, the chain should cover location containing aviano air force base.
Chain: {
"destin florida":[
    "aviano air force base -> location of the base",
    "aviano air force base -> place containing the base ",
]
}
#
#
Question: what major airport is near destin florida?
Topic Entity: "destin florida"
Thought: Firstly, the chain should cover airports near destin florida. Second, it should cover information like the number of runways to compare the size of airports.
Chain: {
"destin florida":[
    "destin florida -> location of nearby airports  -> number of runways in airports",
    "destin florida -> location of nearby airports  -> major airport",
]
}
#
Question: where did laura ingalls wilder live?
Topic Entity: "laura ingalls wilder"
Thought: Firstly, the chain should cover the place where laura ingalls wilder live.
Chain: {
"laura ingalls wilder":[
    "laura ingalls wilder -> place of people live in -> the place located in", 
    "laura ingalls wilder -> place of people live in -> location of the place", 
]
}
#
Question: who played princess leia in star wars movies?
Topic Entity: "princess leia"
Thought: Firstly, the chain should cover the movies portrying princess leia. Secondly, the chain should cover the actors in that movie.
Chain: {
"princess leia":[
    "princess leia -> movies portrying princess leia -> actors in that movie", 
    "princess leia -> characters of movie -> actor of film", 
]
}
#
Question: what are countries in south asia?
Topic Entity: "south asia"
Thought: Firstly, the chain should cover the locations in south asia. Secondly, the chain should cover the country of these locations.
Chain: {
"south asia":[
    "south asia -> the locations in south asia -> the country of these locations", 
    "south asia -> the country in south asia"
]
}
#
Question: what to see outside of paris?
Topic Entity: "paris"
Thought: Firstly, the chain should cover tourist attractions in paris. Or the chain might cover building in paris
Chain: {
"paris":[
    "paris -> tourist attractions in paris", 
    "paris -> building in paris", 
]
}
#