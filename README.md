# calibre_to_kybook2
Sync books and metadata from Calibre to KyBook 2

#### Purpose
The script enables the bulk syncing of books and metadata from [Calibre](https://calibre-ebook.com) to [KyBook 2](http://kybook-reader.com) using Calibre's Wireless Device Connection facility.

#### Rationale
KyBook 2 was supposed to have been superseded by KyBook 3. However, KyBook 3 proved unsuitable for reading PDFs (the bulk of my library) because of a lack of double-page view and in-app rotation lock.

KyBook 2 already supports downloading from Calibre using OPDS, but this script allows more flexibility in mapping Calibre's tags to KyBook 2 subjects and the use of custom cover pages (Calibre's thumbnails).

#### Limitations
* Currently, syncing from KyBook 2 to Calibre is **not** supported. This *may* be added in the future, but it is hoped that KyBook 3 will fully support PDF reading before that happens.
* Syncing is not entirely automatic and some user input is required. (Please follow the instructions below.)
* The script does **not** support KyBook 3. Support for KyBook 3 will be added when full PDF reading has been added (unless support for Calibre Wireless Device Connections is added to KyBook 3 itself).

#### Instructions
##### Backup your Calibre data. (Nothing in Calibre is changed, but it's good to have a backup anyway.)
##### On your server:
1. Start Calibre
2. Select `Connect/share`
3. Select `Start wireless device connection`
4. Select `OK`.
5. In a terminal, run calibre_to_kybook2.py
6. Wait for Calibre to connect to KyBook 2
7. Select the books you wish to sync and select `Send to device`
  * Books will be downloaded to `~/KyBook2/Books`
  * Covers will be downloaded to `~/KyBook2/App/Images`
  * A `.json` file will be downloaded to `~/KyBook2/App/Backups`
  * The location `~/KyBook2/` can be changed by editing `BASE_DIR` in calibre_to_kybook2.py
8. Connect the device to iTunes
9. Select the device that KyBook 2 is installed on
10. Select `File Sharing`
11. Select `KyBook` in the `Apps` column
12. Drag and drop the `App` folder on to the `Documents` column
13. Select `Replace` if prompted
##### In KyBook 2:
1. Select menu (three horizontal lines at top left)
2. Select Settings
3. Select Database
4. Select Restore Database
5. Select the `.json` file
6. Close KyBook 2
##### On your server:
1. Drag and drop the `Books` folder on to KyBook's Documents
2. Select `Replace` if prompted

The order of the steps is important, so if it appears not to work, just repeat.
