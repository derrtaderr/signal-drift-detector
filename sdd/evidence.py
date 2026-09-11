"""Evidence sources.

The `no_hires_since_posting` falsifier needs a fact the signal source does not
carry: whether anyone was hired into the function since the posting went up. v1
**consumes** that evidence, it does not gather it. The source is an interface so
a later adapter (a roster scraper, a job-changes feed) can replace the file
without any check changing.

The distinction that matters everywhere downstream:

* a company **present** in the file was looked at, and ``hires`` is what was found
* a company **absent** returns ``None``, meaning "nobody looked"

Absent must never read as "no hires." That collapse is exactly the failure this
whole tool exists to prevent, so it is a type-level distinction, not a comment.
"""

import json

from .model import DriftError, parse_date


class NullEvidenceSource:
    """Knows nothing about anybody. Every check that needs it fails closed.

    ``configured = False`` is the type-level fact that this stands in for an
    evidence source the operator never supplied. A check must report that as a
    missing source, never as a statement about a company.
    """

    name = "none"
    configured = False

    def observations(self, company):
        return None


class FileEvidenceSource:
    """Hire observations from a JSON file.

    Shape::

        {"companies": {"<company>": {"checked_through": "YYYY-MM-DD",
                                     "hires": [{"function": "gtm",
                                                "date": "YYYY-MM-DD",
                                                "source": "..."}]}}}
    """

    name = "file"
    configured = True

    def __init__(self, path):
        self.path = path
        try:
            with open(path, "r", encoding="utf-8") as handle:
                raw = json.load(handle)
        except FileNotFoundError:
            raise DriftError("evidence file not found: %s" % path)
        except json.JSONDecodeError as exc:
            raise DriftError("evidence file is not valid JSON (%s): %s" % (path, exc))
        except OSError as exc:
            raise DriftError("evidence file could not be read (%s): %s" % (path, exc))

        companies = raw.get("companies")
        if not isinstance(companies, dict):
            raise DriftError(
                "evidence file must carry a 'companies' object: %s" % path
            )

        self._companies = {}
        for company, record in companies.items():
            # A hand-edited evidence file is the normal case, so every shape
            # below is verified before it is used. Guessing at a null record or
            # a bare string where an object belongs is how this died with a
            # stack trace instead of refusing the input.
            if not isinstance(record, dict):
                raise DriftError(
                    "evidence for %s must be an object, got %s"
                    % (company, type(record).__name__)
                )
            raw_hires = record.get("hires", [])
            if not isinstance(raw_hires, list):
                raise DriftError(
                    "hires for %s must be a list, got %s"
                    % (company, type(raw_hires).__name__)
                )
            hires = []
            for hire in raw_hires:
                if not isinstance(hire, dict):
                    raise DriftError(
                        "hire entry for %s must be an object, got %s"
                        % (company, type(hire).__name__)
                    )
                hires.append(
                    {
                        "function": hire.get("function"),
                        "date": parse_date(
                            hire.get("date"), "hire date for %s" % company
                        ),
                        "source": hire.get("source", ""),
                    }
                )
            checked_through = record.get("checked_through")
            self._companies[company] = {
                "hires": hires,
                "checked_through": (
                    parse_date(checked_through, "checked_through for %s" % company)
                    if checked_through
                    else None
                ),
            }

    def observations(self, company):
        return self._companies.get(company)
