You are tasked with generating a structured understanding of a given topic entity based on its possible types. Here is a list of entity types that can describe the attributes of the entity. Your goal is to analyze the entity, understand its context in question, and select the 6 most relevant types that best describe the entity in relation to the problem at hand.
The output should be formatted as a JSON dictionary, where: The key is the topic entity.The value is a list of 6 types that are most relevant to the entity and the problem.
#
Example Input:\n
Question: #A question#
Topic Entity: topic_entity1, topic_entity2\n
Entity Types: {"topic_entity1": ## a list of entity types of topic_entity1 ##, "topic_entity2": ## a list of entity types of topic_entity2 ##}\n
Example Output:\n
Most Relevant Entity Types:
{"topic_entity1": ## a list of 6 most relevant entity types of topic_entity1 ##, "topic_entity2": ## a list of 6 most relevant entity types of topic_entity2 ##}
#
Instructions:
1. Carefully analyze the topic entity and its context in question.
2. From the provided list of entity types, select the 6 types that are most relevant to the entity and the problem.
3. Ensure the output is in the specified JSON dictionary format.
#
Now, based on the question, topic entity and the list of entity types I provide, generate the JSON dictionary as described.\n
