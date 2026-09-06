# Product Vision

Making Kivi a contextually-aware assistant rather than just a plain dictation software involves developing a strong semantic memory system. Rather than just analyzing audio input, Kivi will be able to analyze the flow of the user's work process without having to transfer into a different platform for chatting. The following is a description of how semantic memory can be structured.

## The Shift from Voice-to-Text to Context-Aware Dictation

Dictation apps of the past consider each encounter a single island, considering
only mechanics such as spelling, punctuation, and style. The introduction of
semantics transforms Kivi into an omnipresent assistant that maintains
consistency in its work regardless of project, person, preferences, and
occasions. Instead of functioning as another application in which to travel to
dictate or as a separate chatbot interface, Kivi exists right within the user's
workflow process, silently gathering context through voice commands to
facilitate future dictations.

## Eliminating Repetitive Contextual Overhead

The main return on investment in the semantic memory model is not just to go
through the history of dictations, but to reduce the friction associated with
dictation. Normally, a large amount of time is wasted in re-establishing the
context that is relevant for the particular task being performed. In the
presence of memory that persists, the system fills in these gaps automatically.
Given that from previous dictations it is known that Rahul runs the backend, the
product will be launched on Friday, and a security audit is pending, all the
user has to do is to say, "Create an Acme update."

## Selective Ingestion and Noise Filtering

It is necessary for the efficient functioning of the memory to be very
selective. Kivi needs to remove noise from its environment while storing
information that can be considered high-value, such as domain knowledge,
preferences, state of projects, key events, and social relationships. The phrase
"I prefer concise, three-bullet client emails" is valuable and needs to go into
the persistent store. On the other hand, ephemeral phrases like "I am tired
today" need to be immediately removed. In case of ambiguity, Kivi should be
cautious and get rid of the information.

## Epistemological Discipline and Conflict Management

For the purpose of operational accuracy, Kivi needs to draw a distinction
between speculative inputs and facts that have been proven. Inputs like "Maybe
Arjun will take over the backend" must not be saved as absolute facts. Moreover,
whenever there are new inputs that conflict with older ones, then Kivi needs to
update the active context based on the latest facts. The domain boundaries need
to be maintained and preferences or facts mentioned implicitly during personal
dictations must not be mixed with professional outputs without clear user
alignment.

## User Trust, Provenance and Memory Management

Trust is maintained through proper provenance and user agency rather than
trusting the accuracy of the model itself. In every case, the memory cards
created by Kivi must be connected to the original dictation from which the
memory was extracted by the user. Users need a way to audit and govern their
memories. Specifically, users need access to the control plane of memories where
they can review, edit, override and delete them. Lastly, if Kivi faces a
situation of missing context or ambiguity in the record, then it needs to
explicitly state so instead of hallucinating facts.
