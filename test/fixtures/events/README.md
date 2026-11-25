# Fixtures for the event commands

Every household here is invented: the labels are the names of trees, plants, birds and
landforms, paired at random. None is a real person, and none came from anyone's guest list.

- `households.json`: 40 households, 36 of two people and 4 of one, 76 people in all, as
  `event-setup --households-file` takes them.
- `answers.json`: nine answers of one evening (six yes, two of them with fewer coming than
  the household's party size, and three no), as `event-answer --answers-file` takes them.

`test/events.py` uses both.
