from pathlib import Path
import tempfile
import unittest
from sims4foundry_cli.launch import prepare
from sims4foundry_cli.io import sha256
from sims4foundry_cli.errors import FoundryError


class LaunchPreparationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.game = self.root / "installation"
        (self.game / "Game" / "Bin").mkdir(parents=True)
        (self.game / "__Installer").mkdir()
        self.exe = self.game / "Game" / "Bin" / "TS4_x64.exe"
        self.exe.write_bytes(b"synthetic-not-executable")
        self.launcher = self.exe.with_name("TS4_Launcher_x64.exe")
        self.launcher.write_bytes(b"synthetic-launcher")
        self.manifest = self.game / "__Installer" / "installerdata.xml"
        self.manifest.write_text('<root><contentIDs><contentID>123</contentID></contentIDs><buildMetaData><gameVersion version="fixture"/></buildMetaData></root>')
        self.log = self.root / "EA.log"
        self.success = 'Successful launch. offerKey.offerId=[fixture-offer] slug=[the-sims-4]\n'
        self.request = f'Processing launch request: requestSource[Client] offerId[fixture-offer] contentId[123] exe[{self.launcher}]\n'
        self.log.write_text(self.success + self.request)

    def test_prepares_exact_id_without_writes_or_launch(self):
        before = {str(p): sha256(p) for p in self.root.rglob("*") if p.is_file()}
        result = prepare(self.game, [self.log], sha256(self.exe))
        self.assertFalse(result["launched"])
        self.assertFalse(result["dispatch_available"])
        self.assertFalse(result["cache_written"])
        self.assertEqual("origin2://game/launch?offerIds=123&autoDownload=0", result["url"])
        self.assertEqual(before, {str(p): sha256(p) for p in self.root.rglob("*") if p.is_file()})

    def test_wrong_executable_hash_refused(self):
        with self.assertRaises(FoundryError) as caught:
            prepare(self.game, [self.log], "bad")
        self.assertEqual("wrong_executable", caught.exception.code)

    def test_never_guesses_without_success(self):
        self.log.write_text(self.request)
        with self.assertRaises(FoundryError) as caught:
            prepare(self.game, [self.log], sha256(self.exe))
        self.assertEqual("missing_successful_play", caught.exception.code)

    def test_success_from_other_offer_is_not_reused(self):
        self.log.write_text(self.success.replace("fixture-offer", "another") + self.request)
        with self.assertRaises(FoundryError):
            prepare(self.game, [self.log], sha256(self.exe))

    def test_other_installation_is_not_reused(self):
        self.log.write_text(self.success + self.request.replace(str(self.launcher), "C:\\other\\TS4_Launcher_x64.exe"))
        with self.assertRaises(FoundryError):
            prepare(self.game, [self.log], sha256(self.exe))

    def test_rotation_seam_in_explicit_chronological_order(self):
        older = self.root / "EA.bak"
        older.write_text(self.success)
        self.log.write_text(self.request)
        self.assertEqual("123", prepare(self.game, [older, self.log], sha256(self.exe))["account_launch"]["content_id"])

    def test_content_id_must_exist_in_manifest(self):
        self.log.write_text(self.success + self.request.replace("contentId[123]", "contentId[999]"))
        with self.assertRaises(FoundryError) as caught:
            prepare(self.game, [self.log], sha256(self.exe))
        self.assertEqual("edition_mismatch", caught.exception.code)

    def test_entity_and_non_utf8_metadata_refused(self):
        for raw in (b'<!DOCTYPE root [<!ENTITY e "123">]><root/>', '<root/>'.encode("utf-16")):
            self.manifest.write_bytes(raw)
            with self.assertRaises(FoundryError) as caught:
                prepare(self.game, [self.log], sha256(self.exe))
            self.assertEqual("invalid_installation_manifest", caught.exception.code)
