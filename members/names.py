"""
People's names: first name and surname, kept alongside the full name (`name`) that the rest of
Dojo searches, sorts and prints, plus the preferred name and pronouns a member chooses to give.

`name` stays the full legal name, made from the first name and surname. Code that only knows a
full name can still set `name` (e.g. a CSV import or the signup API): it's split into the parts
(everything before the last word is the first name, the last word the surname).

On classes and registers people show as their register name:
    [preferred name, or first name] [surname] (pronouns)
"""
NAME_FIELDS = ('name', 'first_name', 'surname')
PRONOUN_SUGGESTIONS = ('she/her', 'he/him', 'they/them', 'she/they', 'he/they')


def split_name(full):
    parts = (full or '').split()
    if not parts:
        return '', ''
    if len(parts) == 1:
        return parts[0], ''
    return ' '.join(parts[:-1]), parts[-1]


def join_name(first, surname):
    return ' '.join(part.strip() for part in (first or '', surname or '') if part and part.strip())


def register_name(person):
    shown = join_name(person.preferred_name or person.first_name, person.surname) or person.name
    return f'{shown} ({person.pronouns})' if person.pronouns else shown


class NameParts:
    """Keeps `name` and `first_name`/`surname` in step on save. Mix into a model with all three."""

    @classmethod
    def from_db(cls, db, field_names, values):
        instance = super().from_db(db, field_names, values)
        instance._loaded_names = tuple(instance.__dict__.get(f) for f in NAME_FIELDS)
        return instance

    def sync_names(self):
        loaded = getattr(self, '_loaded_names', None)
        name_changed = loaded is not None and self.name != loaded[0]
        parts_changed = loaded is None or (self.first_name, self.surname) != loaded[1:]
        if (self.first_name or self.surname) and (parts_changed or not name_changed):
            self.name = join_name(self.first_name, self.surname)[:255]
        else:
            self.first_name, self.surname = (part[:150] for part in split_name(self.name))

    def save(self, *args, **kwargs):
        self.sync_names()
        update_fields = kwargs.get('update_fields')
        if update_fields is not None and set(update_fields) & set(NAME_FIELDS):
            kwargs['update_fields'] = set(update_fields) | set(NAME_FIELDS)
        super().save(*args, **kwargs)
        self._loaded_names = tuple(getattr(self, f) for f in NAME_FIELDS)

    @property
    def register_name(self):
        return register_name(self)
