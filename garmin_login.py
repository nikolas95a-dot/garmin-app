#!/usr/bin/env python3
"""Login único a Garmin Connect. Guarda tokens en ~/.garminconnect (o $GARMINTOKENS).
La contraseña no se guarda; solo los tokens, que se renuevan solos."""
import os
from getpass import getpass
from pathlib import Path

from garminconnect import Garmin

TOKENSTORE = str(Path(os.environ.get("GARMINTOKENS", "~/.garminconnect")).expanduser())


def main():
    email = input("Email Garmin: ").strip()
    pwd = getpass("Contraseña: ")
    g = Garmin(email=email, password=pwd,
               prompt_mfa=lambda: input("Código MFA: ").strip())
    g.login(TOKENSTORE)

    # Compatibilidad con versiones que no guardan automáticamente
    if not Path(TOKENSTORE).exists() and hasattr(g, "garth"):
        g.garth.dump(TOKENSTORE)

    print(f"Login OK: {g.get_full_name()}")
    print(f"Tokens guardados en {TOKENSTORE} (tratalos como una contraseña)")


if __name__ == "__main__":
    main()
