"""
Creates the first leadership account. Run this once, right after deploying,
before anyone can log in — every other account gets created through the
/auth/users API by someone with a leadership token, but the very first
leadership account has no one to authorize it.

Usage:
    python -m scripts.create_first_admin
"""

import getpass
from app.models import init_db, SessionLocal, User
from app.auth import hash_password

def main():
    init_db()
    db = SessionLocal()

    existing = db.query(User).filter(User.role == "leadership").first()
    if existing:
        print(f"A leadership account already exists: {existing.email}. Aborting.")
        return

    print("Creating the first leadership account.")
    email = input("Email: ").strip()
    full_name = input("Full name: ").strip()
    password = getpass.getpass("Password: ")

    user = User(
        email=email, full_name=full_name,
        hashed_password=hash_password(password), role="leadership",
    )
    db.add(user)
    db.commit()
    print(f"Created leadership account for {email}. Log in at POST /auth/login.")


if __name__ == "__main__":
    main()
