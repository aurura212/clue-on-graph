You are tasked with generating relation paths to help searching for answers in Freebase based on given question. I will provide you with:
1. A question.
2. One or more topic entity that is central to the question.
3. A set of valuable relations associated with the topic entity.

Your goal is to generate relation paths that start with the topic entity and follow a sequence of relations to help answer the question. 
#
Input Format:
Question: #A question#
Topic Entities: topic_entity1, topic_entity2
Valuable Relations: {"topic_entity1": ['relation','relation',...],...}

Output format:
Thought: #Your thinking process#
Path: {"topic_entity1": ["topic_entity1 -> path1_relation1 -> ..."], "topic_entity2": ["topic_entity2 -> path2_relation1 -> ..."]}
#
Rules for generating the relation paths:
1. Use a json dict as output format, the key of which is the Topic Entities of the Question and the value is an array of array, each inner array is a relation path from the Topic Entity to the answer of the question. You should output different Relation Paths for each Topic Entities, according to the question. The Paths are stored in an array.
2. Each relation path starting from the topic entity must use a relation in valuable relations of corresponding topic entity as the relation1 of relation path. Note: The contents in parentheses are entities linked to the relation. You can refer to the entities in parentheses to generate relation path, but do not generate an relation path with the contents of the parentheses
3. Each question might have one or more topic entities, and each relation path might have one or more relations. Please generate relation path for each topic entity and each topic entity can have at most two relation paths.
4. Do not directly give your predicted answer of the question in relation path.

Now, based on the question, topic entity, and valuable relations I provide, generate relation path to help search for the answer in Freebase. Do not give me other things:
#
Question: Rift Valley Province is located in a nation that uses which form of currency?
Topic Entities:["Rift Valley Province"]
Valuable Relations: {"Rift Valley Province":['location.country.administrative_divisions', 'location.location.contains', 'location.location.area', 'location.administrative_division.country']}
Thought: There is only one topic entity, the answer is constrained by one path. 
For the path from "Rift Valley Province", firstly, it should cover the nation where "Rift Valley Province" is located. Second, it should cover the form of currency used by the nation.
Path: {
"Rift Valley Province":[
    "Rift Valley Province -> location.administrative_division.country -> location.location.geolocation -> location.mailing_address.state_province_region -> location.country.currency_used", 
    "Rift Valley Province -> location.country.administrative_divisions -> location.country.currency",
    "Rift Valley Province -> location.administrative_division.country -> location.country.currency_used"
]
}
#
Question: The country with the National Anthem of Bolivia borders which nations?
Topic Entities:["National Anthem of Bolivia"]
Valuable Relations: {"National Anthem of Bolivia":['music.composition.recordings', 'location.country.national_anthem', 'government.national_anthem_of_a_country.anthem']}
Thought: There is only one topic entity, the answer is constrained by one path. 
For the path from "National Anthem of Bolivia", firstly, it should cover the country with the national athem "National Anthem of Bolivia". Second, it should cover the nations bordering that country.
Path: {
"National Anthem of Bolivia":[
    "National Anthem of Bolivia -> government.national_anthem_of_a_country.anthem -> location.country.national_anthem -> location.adjoining_relationship.adjoins", 
    "National Anthem of Bolivia -> location.country.national_anthem -> government.national_anthem_of_a_country.anthem -> location.location.adjoin_s -> location.adjoining_relationship.adjoins",
    "National Anthem of Bolivia -> location.country.national_anthem -> location.location.adjoin_s"
]
}
#
Question: Who is the director of the movie featured Miley Cyrus and was produced by Tobin Armbrust?
Topic Entities: ["Miley Cyrus", "Tobin Armbrust"]
Valuable Relations: {"Miley Cyrus":['film.person_or_entity_appearing_in_film.films', 'film.film.starring', 'film.actor.film', 'film.performance.actor'], "Tobin Armbrust": ['film.producer.film', 'film.film.produced_by', 'film.film.executor', 'film.producer.films_executive_produced']}
Thought: There are two topic entities, so the answer should be constrained by two relation paths. 
For the path starting from "Miley Cyrus", firstly, it should cover the movies featured Miley Crus. Second, it should cover the directors of the movies.
For the path starting from "Tobin Armbrust", firstly, it should cover the movies produced by Tobin Armbrust. Second, it should cover the directors of the movies.
Finally, the answer of the question should be the intersection of the two paths. 
Path: {
"Miley Cyrus":[
    "Miley Cyrus -> film.film.starring -> film.film_staff.director", 
    "Miley Cyrus -> film.performance.actor -> film.film_maker.director",
],
"Tobin Armbrust":[
    "Tobin Armbrust -> film.film.produced_by -> film.film.director",
    "Tobin Armbrust -> film.film.executor -> film.film_staff.director",
]
}
#
Question: What major religion in the UK has a place of worship named St. Mary's Cathedral, Batticaloa?
Topic Entities:["United Kingdom", "St. Mary's Cathedral, Batticaloa"]
Valuable Relations: {"United Kingdom": ['location.country.first_level_divisions', 'location.statistical_region.religions', 'location.local.religions_religions', 'location.statistical_region.renewable_freshwater_per_capita'], "St. Mary's Cathedral, Batticaloa": ['religion.place_of_worship.religion', 'religion.religious_organization.places_of_worship', 'religion.religious_event.worship']}
Thought: There are two topic entities, so the answer should be constrained by two relation paths. 
For the path starting from "United Kingdom", firstly, it should cover the religions in "United Kingdom". Second, it should cover the majority of the religions.
For the path starting from "St. Mary's Cathedral, Batticaloa", first, it should cover the religion with a place of worship named "St. Mary's Cathedral, Batticaloa".
Finally, the answer of the question should be the intersection of the two paths.
Path: {
"United Kingdom":[
    "United Kingdom -> location.statistical_region.religions -> location.religion_percentage.religion", 
    "United Kingdom -> location.local.religions_religions -> location.religion.major_religions", 
],
"St. Mary's Cathedral, Batticaloa":[
    "St. Mary's Cathedral, Batticaloa -> religion.religious_organization.places_of_worship",
    "St. Mary's Cathedral, Batticaloa -> religion.religious_event.worship -> religion.religious_place.places"
]
}
#
