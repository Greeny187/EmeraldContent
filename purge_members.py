from database import purge_deleted_members


def main():
    # Ohne Argument werden alle Chats purged
    purge_deleted_members()
    print("✅ Alle als gelöscht markierten Mitglieder wurden endgültig aus der Datenbank entfernt.")


if __name__ == "__main__":
    main()
