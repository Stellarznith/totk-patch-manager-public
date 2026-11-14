"""Isolated manager regression tests; no player executable, configuration or save is written."""
import compression.zstd as zstd
import hashlib
import io
import importlib.util
import json
import os
from pathlib import Path
import shutil
import struct
import sys
import tempfile
import unittest
from unittest import mock
import zipfile
import urllib.error

ROOT = Path(__file__).resolve().parents[1]
MANAGER = ROOT / 'src/manager'
sys.path.insert(0, str(MANAGER))
sys.path.insert(0, str(ROOT / 'tools'))
import workspace
spec = importlib.util.spec_from_file_location('manager_core', MANAGER / 'core.py')
m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)


class ManagerTests(unittest.TestCase):
    def setUp(self):
        self.temp = workspace.directory('totk-manager-')
        self.root = Path(self.temp.name)
        self.app = m.APP
        m.APP = self.root / 'manager'
        m.APP.mkdir()
        with zipfile.ZipFile(m.APP / 'recipe.zip', 'w') as fixture:
            fixture.writestr('manifest.json', '{"files": {}}')
        self.idle = m.ensure_idle
        m.ensure_idle = lambda ctx: None

    def tearDown(self):
        m.ensure_idle = self.idle
        m.APP = self.app
        m.LOG=None; m.CANCEL=None;m.NETWORK='auto'
        self.temp.cleanup()

    def fixture(self, version='0.3.10+1234567'):
        game = self.root / '任意 编译 & ! % 版本' / 'totk.exe'
        game.parent.mkdir(parents=True)
        game.write_bytes(b'MZ\0' + version.encode() + b'\0stand-in-not-a-game')
        (game.parent / 'exefs').mkdir(); (game.parent / 'exefs/main').write_bytes(b'local guest data')
        (game.parent / 'romfs').mkdir(); (game.parent / 'romfs/romfs.bin').write_bytes(b'local image')
        return game, m.context(game)

    def test_unknown_host_version_not_gated(self):
        game, ctx = self.fixture('0.99.123+abcdef')
        self.assertEqual(ctx['version'], '0.99.123+abcdef')
        self.assertEqual(m.discover(self.root)[0]['game'], str(game))

    def test_external_configured_data_and_profile(self):
        game, ctx = self.fixture()
        data = self.root / 'other data'; data.mkdir()
        shutil.move(str(game.parent / 'exefs'), data / 'code')
        shutil.move(str(game.parent / 'romfs'), data / 'image')
        (game.parent / 'totk.ini').write_text('exefs="'+str(data/'code')+'"\nromfs="'+str(data/'image')+'"\nprofile_dir="'+str(data/'saves')+'"', encoding='utf-8')
        ctx = m.context(game)
        self.assertEqual(ctx['exefs'], str(data/'code'))
        self.assertEqual(ctx['profile'], str(data/'saves'))

    def test_discover_excludes_sidecars(self):
        game, ctx = self.fixture()
        hidden = game.parent / '.totk-extensions/versions/test'
        shutil.copytree(game.parent/'exefs', hidden/'exefs'); shutil.copytree(game.parent/'romfs', hidden/'romfs')
        shutil.copy2(game, hidden/'totk.exe')
        self.assertEqual(len(m.discover(self.root)), 1)
        with self.assertRaises(ValueError):m.context(hidden/'totk.exe')

    def test_three_way_disjoint_and_upstream_fix(self):
        base = 'a\nb\nc\nd\ne\n'
        self.assertEqual(m.merge_source(base,'a\nPS5\nc\nd\ne\n','a\nb\nc\nd\nUPSTREAM FIX\n'),'a\nPS5\nc\nd\nUPSTREAM FIX\n')

    def test_three_way_conflict_stops(self):
        with self.assertRaises(ValueError):m.merge_source('a\nb\n','a\nPS5\n','a\nNEW API\n')

    def test_three_way_already_merged_and_new_file(self):
        self.assertEqual(m.merge_source('a\n','x\n','x\n'),'x\n')
        self.assertEqual(m.merge_source('','new\n',''),'new\n')

    def recipe(self, basis, pairs, commit=''):
        """Write an adapter shaped like tools/make_recipe.py does: pairs is {file: (base, modified)}."""
        files = {}
        with zipfile.ZipFile(m.APP / ('recipe-%s.zip' % basis), 'w') as z:
            for index, (name, (base, modified)) in enumerate(sorted(pairs.items())):
                z.writestr('%d/base' % index, base)
                z.writestr('%d/modified' % index, modified)
                files[name] = dict(base='%d/base' % index, modified='%d/modified' % index)
            z.writestr('manifest.json', json.dumps(dict(basis=basis, source_commit=commit, files=files)))
        return m.APP / ('recipe-%s.zip' % basis)

    def upstream(self, version, runtime):
        tree = self.root / ('upstream ' + version)
        (tree / 'recomp').mkdir(parents=True); (tree / 'recomp/gen.py').write_text('')
        (tree / 'tools').mkdir(); (tree / 'tools/build_game.py').write_text('')
        (tree / 'VERSION').write_text(version + '\n', encoding='utf-8')
        for name, text in runtime.items():
            p = tree / 'runtime' / name
            p.parent.mkdir(parents=True, exist_ok=True); p.write_text(text, encoding='utf-8')
        return tree

    def test_leftover_adapter_for_another_basis_is_never_applied(self):
        base = 'a\nb\nc\n'
        self.recipe('0.3.13', {'cfg.c': (base, 'a\nPS5\nc\n')})
        source = self.upstream('0.3.4', {'cfg.c': base})
        with self.assertRaises(ValueError) as caught:
            m.recipe_plan(source)
        self.assertIn('低于本机所有适配配方', str(caught.exception))

    def test_newer_upstream_version_adapts_by_content_not_by_number(self):
        base = 'a\nb\nc\n'
        self.recipe('0.4.0', {'cfg.c': (base, 'a\nPS5\nc\n')})
        source = self.upstream('0.5.0', {'cfg.c': base})
        entry, merged, rebased = m.recipe_plan(source)
        self.assertEqual(entry['mode'], 'rebased')
        self.assertEqual(merged['cfg.c'], 'a\nPS5\nc\n')

    def test_upstream_reindent_keeps_the_extension_edit(self):
        base = 'header\nKEEP = 1\nPATCH_ME = 0\ntail\n'
        self.recipe('0.4.0', {'cfg.c': (base, 'header\nKEEP = 1\nPATCH_ME = 7\ntail\n')})
        reindented = '  header\n  KEEP = 1\n  PATCH_ME = 0\n  tail\n'
        source = self.upstream('0.4.1', {'cfg.c': reindented})
        with self.assertRaises(ValueError):
            m.merge_source(base, 'header\nKEEP = 1\nPATCH_ME = 7\ntail\n', reindented)
        entry, merged, rebased = m.recipe_plan(source)
        self.assertEqual(entry['mode'], 'rebased')
        self.assertIn('PATCH_ME = 7', merged['cfg.c'])
        self.assertTrue(rebased)

    def test_upstream_rewriting_the_patched_line_is_refused(self):
        base = 'header\nPATCH_ME = 0\ntail\n'
        self.recipe('0.4.0', {'cfg.c': (base, 'header\nPATCH_ME = 7\ntail\n')})
        source = self.upstream('0.4.1', {'cfg.c': 'header\nPATCH_ME = 9\ntail\n'})
        with self.assertRaises(ValueError) as caught:
            m.recipe_plan(source)
        self.assertIn('已被官方改写', str(caught.exception))

    def test_missing_basis_never_borrows_another_archive_identity(self):
        self.recipe('0.4.0', {'cfg.c': ('a\n', 'a\nb\n')})
        self.assertEqual(m.recipe_id('9.9.9'), 'missing-recipe-9.9.9')
        self.assertRegex(m.recipe_id('0.4.0'), r'^[0-9a-f]{64}$')

    def test_adapter_check_and_sync_need_no_game_and_survive_offline(self):
        self.recipe('0.4.0', {'cfg.c': ('a\n', 'a\nb\n')})
        with mock.patch.object(m,'get_json',side_effect=ValueError('offline')), mock.patch.object(m,'emit') as emit:
            m.main(dict(action='recipe-check',network='auto',cache=str(self.root/'cache')))
            self.assertTrue(any('0.4.0' in call.args[0] for call in emit.call_args_list))
            self.assertEqual(m.try_sync(self.root/'cache'), [])

    def test_vendored_adapter_is_used_without_touching_the_network(self):
        base = 'a\nb\n'
        self.recipe('0.4.0', {'cfg.c': (base, 'a\nPS5\n')})
        cache = self.root/'cache'/'recipes'; cache.mkdir(parents=True)
        shutil.copyfile(m.APP/'recipe-0.4.0.zip', cache/'recipe-0.4.0.zip')
        with mock.patch.object(m,'get_json',side_effect=AssertionError('unexpected network')), mock.patch.object(m,'download',side_effect=AssertionError('unexpected download')):
            self.assertIn('0.4.0', [c['basis'] for c in m.recipe_candidates()])

    def test_duplicate_or_online_updated_recipes_do_not_raise_ambiguity(self):
        # 1) 同内容配方（含时间戳不同的同内容副本）应自动去重，优先保留 APP/recipe.zip
        self.recipe('0.4.0', {'cfg.c': ('a\n', 'a\nold\n')}, commit='1'*40)
        shutil.move(m.APP/'recipe-0.4.0.zip', m.APP/'recipe.zip')
        cache = m.APP/'cache'/'recipes'; cache.mkdir(parents=True)
        shutil.copyfile(m.APP/'recipe.zip', cache/'recipe-0.4.0.zip')
        source = self.upstream('0.4.0', {'cfg.c': 'a\n'})
        m.write_json(source.parent/'verified.json', dict(commit='1'*40))
        chosen = m.select_recipe(source)
        self.assertEqual(chosen['path'], m.APP/'recipe.zip')
        self.assertEqual(m.recipe_id(), m.recipe_id('0.4.0'))

        # 2) 在线缓存 recipe-0.4.0.zip 内容更新时，应优先于随包旧 recipe.zip，且 recipe_id('0.4.0') 口径一致
        updated = self.recipe('0.4.0', {'cfg.c': ('a\n', 'a\nnew\n')}, commit='1'*40)
        shutil.move(updated, cache/'recipe-0.4.0.zip')
        chosen = m.select_recipe(source)
        self.assertEqual(chosen['path'], cache/'recipe-0.4.0.zip')
        self.assertEqual(m.recipe_id('0.4.0'), m.recipe_archive_digest(cache/'recipe-0.4.0.zip'))
        self.assertNotEqual(m.recipe_id(), m.recipe_id('0.4.0'))

        # 3) 安装目录自身存在两份不同内容的同 basis 配方时，仍应拒绝并列出文件名
        extra = self.recipe('0.4.0', {'cfg.c': ('a\n', 'a\nconflict\n')}, commit='1'*40)
        self.assertTrue(extra.is_file())
        with self.assertRaises(ValueError) as caught:
            m.select_recipe(source)
        self.assertIn('recipe.zip', str(caught.exception))
        self.assertIn('recipe-0.4.0.zip', str(caught.exception))

    def test_repeat_install_keeps_the_previous_rollback_point(self):
        game,ctx=self.fixture();before=game.read_bytes();h=m.home(ctx)
        staged=self.root/'prepared.exe';staged.write_bytes(b'MZ\0' + b'0.3.11\0')
        metadata=dict(version='0.3.11',ps5=True,graphics=True)
        first=m.manager_install.install(game.parent,h,{'totk.exe':staged},{'totk.exe'},metadata)
        backups=len(list((h/'backups').iterdir()))
        second=m.manager_install.install(game.parent,h,{'totk.exe':staged},{'totk.exe'},metadata)
        self.assertEqual(second['backup'],first['backup'])
        self.assertEqual(len(list((h/'backups').iterdir())),backups)
        m.manager_install.rollback(game.parent,h)
        self.assertEqual(game.read_bytes(),before)

    def test_personal_backup_carries_mods_and_icon_styles(self):
        game,ctx=self.fixture();root=game.parent
        (root/'mods/PlayerMod').mkdir(parents=True);(root/'mods/PlayerMod/a.pchtxt').write_text('mod')
        (root/(m.ICON_FOLDER+'-stavaas')).mkdir();(root/(m.ICON_FOLDER+'-stavaas/b.bin')).write_bytes(b'icon')
        m.write_json(m.home(ctx)/'settings.json',dict(icon_style='stavaas'))
        target=m.backup_personal(ctx)
        with zipfile.ZipFile(target/'模组与设置.zip') as z:
            names=set(z.namelist())
        self.assertIn('mods/PlayerMod/a.pchtxt',names)
        self.assertIn(m.ICON_FOLDER+'-stavaas/b.bin',names)
        self.assertIn('manager/settings.json',names)

    def test_zip_path_traversal_rejected(self):
        for name in ['../escape','/outside','x\\..\\y','C:/outside']:
            with self.assertRaises(ValueError):m.inside(self.root,name)
        self.assertEqual(m.inside(self.root,'正常/a.txt'),self.root/'正常/a.txt')

    def test_health_tamper_and_mutable_settings(self):
        game, ctx = self.fixture(); h=m.home(ctx); f=game.parent/'addon.dll'; f.write_bytes(b'installed component')
        m.write_json(h/'active.json',dict(mode='direct',version='0.3.10',ps5=True,graphics=True,files={'addon.dll':m.digest(f)}))
        self.assertEqual(m.health(ctx)['status'],'正常')
        (f.parent/'totk.ini').write_text('user changes settings')
        self.assertEqual(m.health(ctx)['status'],'正常')
        f.write_bytes(b'corrupt'); self.assertEqual(m.health(ctx)['status'],'需要修复')

    def test_explicit_rebuild_uses_installed_program_version(self):
        game,ctx=self.fixture('0.3.7')
        m.write_json(m.home(ctx)/'active.json',dict(version='0.3.10'))
        for action in ['rebuild-current']:
            with mock.patch.object(m,'component_release',return_value=({},{})) as check, mock.patch.object(m,'build') as build:
                m.main(dict(action=action,game=str(game),cache=str(self.root/'cache')))
                check.assert_called_once_with(self.root/'cache','0.3.7');self.assertEqual(build.call_count,1)

    def test_rollback_and_disable_preserve_official(self):
        game, ctx = self.fixture(); before=game.read_bytes();h=m.home(ctx)
        staged=self.root/'prepared.exe';staged.write_bytes(b'MZ\0' + b'0.3.11\0')
        metadata=dict(version='0.3.11',ps5=True,graphics=True)
        m.manager_install.install(game.parent,h,{'totk.exe':staged},{'totk.exe'},metadata)
        self.assertNotEqual(game.read_bytes(),before)
        m.main(dict(action='rollback',game=str(game),cache=str(self.root/'cache')))
        self.assertEqual(game.read_bytes(),before)
        m.manager_install.install(game.parent,h,{'totk.exe':staged},{'totk.exe'},metadata)
        m.main(dict(action='disable',game=str(game),cache=str(self.root/'cache')))
        self.assertEqual(m.active(ctx),{});self.assertEqual(game.read_bytes(),before)

    def test_direct_install_failure_restores_all_files(self):
        game,ctx=self.fixture();before=game.read_bytes();h=m.home(ctx)
        stage=self.root/'staging';stage.mkdir();exe=stage/'totk.exe';exe.write_bytes(b'MZ\0new build\0');dll=stage/'z.dll';dll.write_bytes(b'new runtime')
        ini=game.parent/'totk.ini';ini.write_text('user settings')
        real=m.manager_install.replace_file
        def fail_second(source,target):
            if source==dll:raise OSError('simulated file lock')
            return real(source,target)
        with mock.patch.object(m.manager_install,'replace_file',side_effect=fail_second):
            with self.assertRaises(OSError):m.manager_install.install(game.parent,h,{'totk.exe':exe,'z.dll':dll},{'totk.exe','z.dll'},dict(version='0.3.11',ps5=True,graphics=True))
        self.assertEqual(game.read_bytes(),before);self.assertEqual(ini.read_text(),'user settings')
        self.assertFalse((game.parent/'z.dll').exists());self.assertFalse((h/'pending.json').exists())

    def test_direct_pending_recovery_and_moved_folder(self):
        game,ctx=self.fixture();before=game.read_bytes();h=m.home(ctx)
        staged=self.root/'prepared.exe';staged.write_bytes(b'MZ\0new\0')
        state=m.manager_install.install(game.parent,h,{'totk.exe':staged},{'totk.exe'},dict(version='0.3.11',ps5=True,graphics=True))
        m.write_json(h/'pending.json',dict(backup=state['backup']))
        moved=self.root/'moved game';game.parent.rename(moved)
        m.manager_install.recover(moved,moved/'.totk-extensions',lambda message:None)
        self.assertEqual((moved/'totk.exe').read_bytes(),before)

    def test_direct_rollback_keeps_later_third_party_changes(self):
        game,ctx=self.fixture();h=m.home(ctx);source=self.root/'addon.dll';source.write_bytes(b'ours')
        state=m.manager_install.install(game.parent,h,{'addon.dll':source},{'addon.dll'},dict(version='0.3.10',ps5=True,graphics=True))
        (game.parent/'addon.dll').write_bytes(b'later mod')
        m.manager_install.rollback(game.parent,h)
        self.assertFalse((game.parent/'addon.dll').exists())
        self.assertEqual((h/state['backup']/'later-changes/addon.dll').read_bytes(),b'later mod')

    def test_cleanup_only_recognized_external_files(self):
        game,ctx=self.fixture();root=game.parent
        (root/'dxgi.dll').write_bytes(b'injector');(root/'my-important.txt').write_text('keep')
        (root/'mods/a/romfs').mkdir(parents=True);(root/'mods/a/romfs/a.bin').write_bytes(b'mod')
        found={p.relative_to(root).as_posix() for p in m.cleanup_candidates(ctx)}
        self.assertEqual(found,{'dxgi.dll','mods/a/romfs/a.bin'})
        self.assertTrue((root/'my-important.txt').exists());self.assertTrue((root/'romfs/romfs.bin').exists())

    def test_official_restore_can_replace_local_index_with_rollback(self):
        game,ctx=self.fixture();root=game.parent;index=root/'romfs-index.bin';index.write_bytes(b'local old index')
        fresh=self.root/'fresh.index';fresh.write_bytes(b'local regenerated index')
        m.manager_install.install(root,m.home(ctx),{'romfs-index.bin':fresh},{'romfs-index.bin'},dict(version='0.3.10',ps5=False,graphics=False,official=True,local_index_regenerated=True))
        self.assertEqual(index.read_bytes(),b'local regenerated index')
        m.manager_install.rollback(root,m.home(ctx));self.assertEqual(index.read_bytes(),b'local old index')

    def test_game_menu_language_preserves_other_settings(self):
        game,ctx=self.fixture();ini=game.parent/'totk.ini';ini.write_text('[display]\ndlss_preset = m\n[system]\nmenu_lang = zh-Hans\n')
        m.main(dict(action='language',game=str(game),cache=str(self.root/'cache'),language='en'))
        self.assertIn('menu_lang = en',ini.read_text());self.assertIn('dlss_preset = m',ini.read_text())
        self.assertEqual(len(list((m.home(ctx)/'backups').glob('menu-language-*.ini'))),1)
        m.main(dict(action='language',game=str(game),cache=str(self.root/'cache'),language='auto'))
        self.assertEqual(m.ini_values(ini)['MENU_LANG'],'')
        self.assertIn('dlss_preset = m',ini.read_text())

    def test_official_restore_then_install_components_keeps_index(self):
        game,ctx=self.fixture();root=game.parent;h=m.home(ctx)
        index=root/'romfs-index.bin';index.write_bytes(b'old index')
        fresh=self.root/'new.index';fresh.write_bytes(b'official local index')
        stock=self.root/'stock.exe';stock.write_bytes(b'MZ official')
        addon=self.root/'addon.exe';addon.write_bytes(b'MZ TOTK_EXTENSIONS_V1:3:END')
        m.manager_install.install(root,h,{'totk.exe':stock,'romfs-index.bin':fresh},{'totk.exe','romfs-index.bin'},dict(version='0.3.10',ps5=False,graphics=False,official=True,local_index_regenerated=True))
        m.manager_install.install(root,h,{'totk.exe':addon},{'totk.exe'},dict(version='0.3.10',ps5=True,graphics=True,official=False))
        self.assertEqual(index.read_bytes(),b'official local index')
        self.assertEqual(m.binary_features(game),3)
        self.assertIn('已安装',m.components(ctx)['ps5'])
        m.manager_install.rollback(root,h)
        self.assertEqual(game.read_bytes(),stock.read_bytes())
        self.assertEqual(index.read_bytes(),b'official local index')
        m.manager_install.rollback(root,h)
        self.assertEqual(index.read_bytes(),b'old index')

    def test_actual_component_marker_overrides_stale_record(self):
        game,ctx=self.fixture();game.write_bytes(b'MZ TOTK_EXTENSIONS_V1:3:END')
        m.write_json(m.home(ctx)/'active.json',dict(mode='direct',ps5=False,graphics=False,version='0.3.10',files={'totk.exe':'0'*64}))
        result=m.components(ctx)
        self.assertIn('已安装',result['ps5']);self.assertEqual(result['detected_features'],3)
        self.assertFalse(result['record_matches_executable'])

    def test_component_markers_separate_feature_sets(self):
        game,ctx=self.fixture()
        (game.parent/'romfs-patches-ps5').mkdir()
        (game.parent/'romfs-patches-ps5/manifest.tsv').write_text('fixture')
        for flags in range(4):
            game.write_bytes(('MZ TOTK_EXTENSIONS_V1:'+str(flags)+':END').encode())
            self.assertEqual(m.binary_features(game),flags)
            self.assertEqual(m.components(ctx)['ps5'].startswith('已安装'),bool(flags&1))
            self.assertEqual(m.components(ctx)['graphics'].startswith('已安装'),bool(flags&2))
            self.assertEqual(m.components(ctx)['icons'].startswith('已安装'),bool(flags&1))

    def test_component_markers_report_the_icon_styles_on_disk(self):
        game,ctx=self.fixture();root=game.parent
        self.assertEqual(m.components(ctx)['icon_styles'],[])
        (root/'romfs-patches-ps5-alerion').mkdir()
        (root/'romfs-patches-ps5-stavaas').mkdir()
        (root/'romfs-patches-ps5').mkdir()
        (root/'romfs-patches-ps5/manifest.tsv').write_text('fixture')
        self.assertEqual(m.components(ctx)['icon_styles'],
                         ['romfs-patches-ps5','romfs-patches-ps5-alerion','romfs-patches-ps5-stavaas'])

    def icon_app(self):
        """An app folder whose assets.zip holds the shared white set and the two icon mods' six files."""
        app=self.root/'icon-app';app.mkdir()
        rows={'romfs-patches-ps5/manifest.tsv':b'# PS5 white\n','romfs-patches-ps5/Font/Font.Nin_NX_NVN.bfarc.zs':b'white font',
              'romfs-patches-ps5/Mals/EUen.Product.142.sarc.zs':b'shared messages'}
        for suffix,font in (('-stavaas',b'stavaas font'),('-alerion',b'alerion font')):
            rows['romfs-patches-ps5'+suffix+'/Font/Font.Nin_NX_NVN.bfarc.zs']=font
            rows['romfs-patches-ps5'+suffix+'/manifest.tsv']=b'# PS5 '+suffix.encode()+b'\n'
        metadata=[]
        with zipfile.ZipFile(app/'assets.zip','w') as z:
            for i,(path,data) in enumerate(sorted(rows.items())):
                z.writestr(str(i),zstd.compress(data,level=1))
                metadata.append(dict(path=path,kind=2,entry=str(i),sha256=hashlib.sha256(data).hexdigest()))
            z.writestr('manifest.json',json.dumps(metadata))
        return app

    def test_icon_styles_write_one_folder_or_all_three(self):
        game,ctx=self.fixture();app=self.icon_app()
        (game.parent/'romfs/index.json').write_text(json.dumps(dict(files=[])))
        with mock.patch.object(m,'APP',app):
            for style,folders in ((None,['romfs-patches-ps5','romfs-patches-ps5-alerion','romfs-patches-ps5-stavaas']),
                                  ('white',['romfs-patches-ps5']),
                                  ('stavaas',['romfs-patches-ps5-stavaas']),
                                  ('alerion',['romfs-patches-ps5-alerion']),
                                  ('unknown',['romfs-patches-ps5'])):
                out=self.root/'out'/str(style)
                m.assets(ctx,out,True,False,style)
                self.assertEqual(sorted(p.name for p in out.iterdir() if p.is_dir()),folders,style)
                for folder in folders:
                    font=(out/folder/'Font/Font.Nin_NX_NVN.bfarc.zs').read_bytes()
                    self.assertIn(font,(b'white font',b'stavaas font',b'alerion font'),(style,folder))
                # A mod folder always carries the shared message archive and the manifest the game reads.
                self.assertTrue((out/folders[0]/'Mals/EUen.Product.142.sarc.zs').is_file(),style)
                self.assertTrue((out/folders[0]/'manifest.tsv').is_file(),style)

    def test_icon_style_round_trips_through_the_installed_pack(self):
        game,ctx=self.fixture();app=self.icon_app()
        (game.parent/'romfs/index.json').write_text(json.dumps(dict(files=[])))
        with mock.patch.object(m,'APP',app):
            self.assertIsNone(m.icons_style(ctx))
            for style in ('stavaas','alerion','white'):
                m.install_icons(ctx,style)
                pack=game.parent/'mods/PS5_Icons_Manager'
                self.assertEqual(m.icons_style(ctx),style)
                self.assertEqual(m.components(ctx)['icon_style'],style)
                self.assertEqual(m.icons_health(ctx),'正常')
                # The manifest describes the pack, so it sits beside romfs and not inside it.
                self.assertTrue((pack/'manifest.tsv').is_file())
                self.assertFalse((pack/'romfs/manifest.tsv').exists())
                self.assertTrue((pack/'romfs/Font/Font.Nin_NX_NVN.bfarc.zs').is_file())
                self.assertEqual(m.icon_style(json.loads((pack/'.totk-manager.json').read_text())['style']),style)
            # The previous pack is kept, so a style change is reversible.
            self.assertEqual(len(list((m.home(ctx)/'icons-history').iterdir())),2)

    def test_icon_install_writes_the_three_folders_the_menu_reads(self):
        """F1 → 操作 → 按键提示 reads the three folders beside romfs-index.bin, so installing fills all of them."""
        game,ctx=self.fixture();app=self.icon_app()
        game.write_bytes(b'MZ TOTK_EXTENSIONS_V1:3:END')
        (game.parent/'romfs/index.json').write_text(json.dumps(dict(files=[])))
        (game.parent/'romfs-patches-ps5').mkdir();(game.parent/'romfs-patches-ps5/old.txt').write_text('stale')
        with mock.patch.object(m,'APP',app):
            m.install_icons(ctx,'stavaas')
        root=game.parent
        for folder in ('romfs-patches-ps5','romfs-patches-ps5-stavaas','romfs-patches-ps5-alerion'):
            self.assertTrue((root/folder/'manifest.tsv').is_file(),folder)
        # The selected style is still what the standalone pack carries, and the folder it replaced is kept aside.
        self.assertEqual(m.icons_style(ctx),'stavaas')
        self.assertTrue((root/'mods/PS5_Icons_Manager/romfs/Font/Font.Nin_NX_NVN.bfarc.zs').read_bytes(),b'stavaas font')
        self.assertFalse((root/'romfs-patches-ps5/old.txt').exists())
        kept=[p.name for p in (m.home(ctx)/'icons-history').iterdir()]
        self.assertTrue(any(name.startswith('overlay-romfs-patches-ps5-') for name in kept),kept)

    def test_icon_install_leaves_the_folders_alone_without_the_ps5_component(self):
        """An exe without the PS5 component never loads an icon overlay, so the folders are not written for it."""
        game,ctx=self.fixture();app=self.icon_app()
        (game.parent/'romfs/index.json').write_text(json.dumps(dict(files=[])))
        with mock.patch.object(m,'APP',app):
            m.install_icons(ctx,'alerion')
        self.assertFalse((game.parent/'romfs-patches-ps5').exists())
        self.assertTrue((game.parent/'mods/PS5_Icons_Manager/manifest.tsv').is_file())

    def test_installed_icon_pack_is_never_overwritten_by_a_foreign_folder(self):
        game,ctx=self.fixture();app=self.icon_app();foreign=game.parent/'mods/PS5_Icons_Manager'
        (game.parent/'romfs/index.json').write_text(json.dumps(dict(files=[])))
        foreign.mkdir(parents=True);(foreign/'romfs').mkdir();(foreign/'romfs/keep.txt').write_text('player file')
        with mock.patch.object(m,'APP',app):
            with self.assertRaises(ValueError):m.install_icons(ctx,'stavaas')
        self.assertEqual((foreign/'romfs/keep.txt').read_text(),'player file')

    def test_cleanup_offers_every_icon_style_folder(self):
        game,ctx=self.fixture()
        for name in ('romfs-patches-ps5','romfs-patches-ps5-stavaas','romfs-patches-ps5-alerion'):
            (game.parent/name).mkdir();(game.parent/name/'manifest.tsv').write_text('fixture')
        found={p.relative_to(game.parent).parts[0] for p in m.cleanup_candidates(ctx)}
        self.assertTrue({'romfs-patches-ps5','romfs-patches-ps5-stavaas','romfs-patches-ps5-alerion'} <= found,found)

    def test_prepared_cache_validates_payload_before_reuse(self):
        cache=self.root/'cache';deployment=cache/'staging/ready';deployment.mkdir(parents=True)
        exe=deployment/'totk.exe';exe.write_bytes(b'MZ payload')
        m.write_json(cache/'prepared/key.json',dict(deployment='staging/ready',files={'totk.exe':m.digest(exe)}))
        self.assertEqual(m.load_prepared(cache,'key')['path'],deployment)
        exe.write_bytes(b'corrupt')
        self.assertIsNone(m.load_prepared(cache,'key'))

    def test_cached_release_metadata_avoids_network_for_component_apply(self):
        cache=self.root/'cache';saved=dict(tag_name='v0.3.10',draft=False,prerelease=False,assets=[dict(name='TotK-Setup-0.3.10-windows.zip',digest='sha256:'+'a'*64)])
        m.write_json(cache/'release-metadata/v0.3.10.json',saved)
        with mock.patch.object(m,'release',side_effect=AssertionError('unexpected network')):
            info,asset=m.component_release(cache,'0.3.10')
            self.assertEqual(info['tag_name'],'v0.3.10')

    def test_default_component_actions_never_start_source_builds(self):
        game,ctx=self.fixture()
        with mock.patch.object(m,'install_prebuilt') as install,mock.patch.object(m,'build',side_effect=AssertionError('unexpected compile')),mock.patch.object(m,'release',side_effect=AssertionError('unexpected network')):
            for action in ('apply','repair'):m.main(dict(action=action,game=str(game),cache=str(self.root/'cache')))
            self.assertEqual(install.call_count,2)

    def test_repair_preserves_current_component_selection(self):
        game,ctx=self.fixture();m.write_json(m.home(ctx)/'active.json',dict(mode='direct',version='0.3.10',ps5=True,graphics=False))
        with mock.patch.object(m,'install_prebuilt') as install:
            m.main(dict(action='repair',game=str(game),cache=str(self.root/'cache'),ps5=True,graphics=True))
            self.assertEqual(install.call_args.args[1:3],(True,False))

    def test_missing_prebuilt_bundle_does_not_fall_back_to_download(self):
        game,ctx=self.fixture();before=game.read_bytes()
        with mock.patch.object(m,'APP',self.root/'empty-app'),mock.patch.object(m,'download',side_effect=AssertionError('unexpected download')):
            with self.assertRaises(ValueError):m.main(dict(action='apply',game=str(game),cache=str(self.root/'cache')))
        self.assertEqual(game.read_bytes(),before)

    def test_first_prebuilt_install_offline_without_compiler_or_prewarm(self):
        game,ctx=self.fixture('0.3.10+abcdef');before=game.read_bytes();root=game.parent
        (root/'romfs-index.bin').write_bytes(struct.pack('<8sQQ',b'TOTKRFS1',0,24))
        (root/'totk.ini').write_text('menu_lang=zh-Hans\nframegen=off\n');ini=(root/'totk.ini').read_bytes()
        app=self.root/'bundled-app';payload=app/'prebuilt';payload.mkdir(parents=True)
        exe=b'MZ\x00' + b'0.3.10\x00TOTK_EXTENSIONS_V1:3:END';checker=payload/'hostcheck.exe';checker.write_bytes(b'checker')
        manifest=dict(schema=1,version='0.3.10',source_commit='1'*40,recipe_id='test recipe',hostcheck_sha256=m.digest(checker),games={'1.4.3':dict(exefs={'main':m.digest(Path(ctx['exefs'])/'main')},variants={})})
        for label,data in [('common',{'SDL3.dll':b'dll'}),('v3-3',{'totk.exe':exe})]:
            archive=payload/(label+'.zip')
            with zipfile.ZipFile(archive,'w') as z:
                for name,content in data.items():z.writestr(name,content)
            item=dict(path=archive.name,sha256=m.digest(archive),files={name:hashlib.sha256(content).hexdigest() for name,content in data.items()})
            if label=='common':manifest['games']['1.4.3']['common']=item
            else:manifest['games']['1.4.3']['variants'][label]=item
        m.write_json(payload/'manifest.json',manifest)
        def hostcheck(args,**kwargs):
            self.assertEqual(Path(args[0]),checker)
            return mock.Mock(returncode=0,stdout=json.dumps(dict(x86_64_v2=True,x86_64_v3=True)))
        with mock.patch.object(m,'APP',app),mock.patch.object(m,'recipe_id',return_value='test recipe'),mock.patch.object(m,'assets',return_value=0),mock.patch.object(m,'run') as probes,mock.patch.object(m.subprocess,'run',side_effect=hostcheck),mock.patch.object(m,'toolkit',side_effect=AssertionError('unexpected toolkit')),mock.patch.object(m,'download',side_effect=AssertionError('unexpected download')):
            m.main(dict(action='apply',game=str(game),cache=str(self.root/'cache'),ps5=True,graphics=True))
            self.assertEqual([call.args[0][1] for call in probes.call_args_list],['--probe-cfg','--probe-loader'])
        self.assertEqual(game.read_bytes(),exe);self.assertEqual((root/'totk.ini').read_bytes(),ini)
        self.assertNotEqual(game.read_bytes(),before);self.assertEqual(m.active(ctx)['install_method'],'prebuilt')

    def test_prebuilt_entry_enforces_real_bundled_recipe_id(self):
        game,ctx=self.fixture('0.4.0+abcdef')
        (game.parent/'romfs-index.bin').write_bytes(struct.pack('<8sQQ',b'TOTKRFS1',0,24))
        app=self.root/'real-bundled-app';payload=app/'prebuilt';payload.mkdir(parents=True)
        recipe_file=self.recipe('0.4.0',{'cfg.c':('a\n','a\nb\n')})
        shutil.move(recipe_file,app/'recipe.zip')
        with zipfile.ZipFile(app/'assets.zip','w') as az:az.writestr('manifest.json','[]')
        with mock.patch.object(m,'APP',app):
            real_rid=m.recipe_id(); real_aid=m.assets_id()
        exe=b'MZ\x00' + b'0.4.0\x00TOTK_EXTENSIONS_V1:3:END';checker=payload/'hostcheck.exe';checker.write_bytes(b'checker')
        manifest=dict(schema=1,version='0.4.0',source_commit='1'*40,recipe_id=real_rid,assets_id=real_aid,
                      hostcheck_sha256=m.digest(checker),games={'1.4.3':dict(exefs={'main':m.digest(Path(ctx['exefs'])/'main')},variants={})})
        for label,data in [('common',{'SDL3.dll':b'dll'}),('v3-3',{'totk.exe':exe})]:
            archive=payload/(label+'.zip')
            with zipfile.ZipFile(archive,'w') as z:
                for name,content in data.items():z.writestr(name,content)
            item=dict(path=archive.name,sha256=m.digest(archive),files={name:hashlib.sha256(content).hexdigest() for name,content in data.items()})
            if label=='common':manifest['games']['1.4.3']['common']=item
            else:manifest['games']['1.4.3']['variants'][label]=item
        m.write_json(payload/'manifest.json',manifest)
        def hostcheck(args,**kwargs):
            return mock.Mock(returncode=0,stdout=json.dumps(dict(x86_64_v2=True,x86_64_v3=True)))
        with mock.patch.object(m,'APP',app),mock.patch.object(m,'assets',return_value=0),mock.patch.object(m,'run'),mock.patch.object(m.subprocess,'run',side_effect=hostcheck):
            m.install_prebuilt(ctx,True,True,self.root/'cache')
        self.assertEqual(game.read_bytes(),exe)
        # 当 prebuilt/manifest.json 的 recipe_id 与随包 recipe.zip 不一致时，真实入口必须拦截并报错
        manifest['recipe_id']='0'*64
        m.write_json(payload/'manifest.json',manifest)
        with mock.patch.object(m,'APP',app):
            with self.assertRaisesRegex(ValueError,'随包程序与修复配方不匹配'):
                m.install_prebuilt(ctx,True,True,self.root/'cache2')

    def test_ps5_assets_compatibility_blocks_missing_143_resources_cleanly(self):
        game,ctx=self.fixture('0.4.0+abcdef')
        app=self.root/'ps5-compat-app';app.mkdir()
        with zipfile.ZipFile(app/'assets.zip','w') as az:
            az.writestr('0',zstd.compress(b'common',level=1))
            az.writestr('manifest.json',json.dumps([dict(path=m.ICON_FOLDER+'/'+m.PS5_ACTION_COMMON,kind=2,entry='0',sha256=hashlib.sha256(b'common').hexdigest())]))
        (game.parent/'romfs/index.json').write_text(json.dumps(dict(files=[dict(path='UI/LayoutArchive/Common.Product.120.Nin_NX_NVN.blarc.zs',offset=0,size=1)])))
        with mock.patch.object(m,'APP',app),mock.patch.object(m,'mod_game_version',return_value='1.2.1'),mock.patch.object(m,'toolkit',side_effect=AssertionError('should stop before toolkit')):
            with self.assertRaisesRegex(ValueError,r'1\.4\.3'):
                m._build(ctx,True,True,dict(tag_name='v0.4.0'),dict(name='a.zip',digest='sha256:'+'0'*64),self.root/'cache')
            with self.assertRaisesRegex(ValueError,r'1\.4\.3'):
                m.assets(ctx,self.root/'out',True,False)
        with mock.patch.object(m,'APP',app),mock.patch.object(m,'mod_game_version',return_value=''):
            with self.assertRaisesRegex(ValueError,r'Common\.Product\.140'):
                m.assets(ctx,self.root/'out2',True,False)
        common_file=self.root/'out3'/m.ICON_FOLDER/m.PS5_ACTION_COMMON
        common_file.parent.mkdir(parents=True,exist_ok=True); common_file.write_bytes(b'dummy')
        with self.assertRaisesRegex(ValueError,r'1\.4\.3'):
            m.ps5_action_backend().repair(self.root/'out3',[m.ICON_FOLDER],{},io.BytesIO(b''),m.ICON_FOLDER,lambda:None)

    def test_prebuilt_refuses_newer_or_unrecognized_program_before_staging(self):
        game,ctx=self.fixture()
        for version,message in [('0.3.14+abcdef','已阻止降级'),('custom','无法可靠识别'),
                                ('0.3.12\0'+'0.3.14','无法可靠识别')]:
            with self.subTest(version=version):
                program=b'MZ\0'+version.encode()+b'\0'
                game.write_bytes(program)
                with mock.patch.object(m,'bundled_components',return_value=dict(version='0.3.13')),mock.patch.object(m.subprocess,'run') as hostcheck:
                    with self.assertRaisesRegex(ValueError,message):
                        m.install_prebuilt(ctx,False,True,self.root/'cache')
                    hostcheck.assert_not_called()
                self.assertEqual(game.read_bytes(),program)

    def test_prebuilt_compatibility_uses_runtime_version_without_build_suffix(self):
        self.assertEqual(m.prebuilt_version_status('0.4.0+7a37fc3c', '0.4.0'), 'compatible')
        self.assertEqual(m.prebuilt_version_status('0.4.0+7a37fc3c', '0.3.13'), 'newer')
        self.assertEqual(m.prebuilt_version_status('未知（不猜测）', '0.4.0'), 'unknown')
        self.assertEqual(m.prebuilt_version_status('0.4.0+7a37fc3c', ''), 'unavailable')

    def test_repacked_assets_keep_component_cache_identity(self):
        app=self.root/'app';app.mkdir()
        with mock.patch.object(m,'APP',app):
            for stamp in [(2020,1,1,0,0,0),(2026,11,5,0,0,0)]:
                with zipfile.ZipFile(app/'assets.zip','w') as z:z.writestr(zipfile.ZipInfo('test',date_time=stamp),b'identical payload')
                if stamp[0]==2020:before=m.assets_id()
                else:self.assertEqual(m.assets_id(),before)

    def test_official_launch_does_not_disable_standalone_icons(self):
        game,ctx=self.fixture()
        with mock.patch.object(m,'active',return_value=dict(mode='direct',ps5=False)),mock.patch.object(m,'health',return_value=dict(status='正常')),mock.patch.object(m.subprocess,'Popen') as popen:
            popen.return_value.pid=123
            popen.return_value.wait.side_effect=m.subprocess.TimeoutExpired('totk.exe',8)
            m.launch(ctx)
            self.assertNotIn('TOTK_MODS_DISABLED',popen.call_args.kwargs['env'])

    def test_launch_refuses_second_game_before_starting_process(self):
        game,ctx=self.fixture()
        with mock.patch.object(m,'ensure_idle',side_effect=ValueError('already running')),mock.patch.object(m.subprocess,'Popen') as popen:
            with self.assertRaises(ValueError):m.launch(ctx)
            popen.assert_not_called()

    def test_launch_reports_prewarm_before_window_exists(self):
        game,ctx=self.fixture()
        def prewarming(args,**kwargs):
            kwargs['stdout'].write(b'[host] gfx_shader: prewarm: 520 of 29543 shaders, 520 translated, 4024 ms\n')
            process=mock.Mock(pid=123)
            process.wait.side_effect=m.subprocess.TimeoutExpired('totk.exe',8)
            return process
        with mock.patch.object(m.subprocess,'Popen',side_effect=prewarming),mock.patch.object(m,'emit') as emit:
            m.launch(ctx)
            self.assertIn('正在预热着色器（520 / 29543）',emit.call_args.args[0])

    def test_recipe_update_is_detected_without_blocking_healthy_game(self):
        game,ctx=self.fixture();m.write_json(m.home(ctx)/'active.json',dict(mode='direct',version='0.3.10',ps5=True,graphics=True,recipe_id='previous recipe',files={'totk.exe':m.digest(game)}))
        status=m.health(ctx)
        self.assertEqual(status['status'],'正常');self.assertTrue(status['recipe_outdated'])
        with mock.patch.object(m.subprocess,'Popen') as popen:
            popen.return_value.wait.side_effect=m.subprocess.TimeoutExpired('totk.exe',8)
            popen.return_value.pid=789;m.launch(ctx)
            popen.assert_called_once()

    def test_safe_launch_overrides_only_this_run(self):
        game,ctx=self.fixture();ini=game.parent/'totk.ini';ini.write_text('dlaa = 1\nframegen = fsr3\ntemporal_engine = fsr3\nmenu_lang = zh-Hans\n')
        before=ini.read_bytes()
        with mock.patch.object(m.subprocess,'Popen') as popen:
            popen.return_value.wait.side_effect=m.subprocess.TimeoutExpired('totk.exe',8)
            popen.return_value.pid=456;m.launch(ctx,safe=True)
            env=popen.call_args.kwargs['env']
            self.assertEqual(env['TOTK_DLAA'],'0');self.assertEqual(env['TOTK_FRAMEGEN'],'off')
            self.assertEqual(env['TOTK_TEMPORAL_ENGINE'],'dlss')
            self.assertEqual(env['TOTK_VK_RECORDER'],'0')
            self.assertEqual(env['TOTK_PIPE_PREBUILD'],'0')
            self.assertNotIn('TOTK_HDR',env)
        self.assertEqual(ini.read_bytes(),before)

    def test_sdr_launch_keeps_graphics_settings_and_normal_failure_log(self):
        game,ctx=self.fixture()
        ini=game.parent/'totk.ini';ini.write_bytes(b'hdr=1\nframegen=fsr3\n')
        logs=m.home(ctx)/'logs'
        with mock.patch.object(m.subprocess,'Popen') as popen:
            popen.return_value.wait.return_value=0xc0000005
            with self.assertRaisesRegex(ValueError,'0xC0000005'):
                m.launch(ctx)
            (logs/'game.log').write_bytes(b'original failure')
            popen.return_value.pid=123
            popen.return_value.wait.side_effect=m.subprocess.TimeoutExpired('totk.exe',8)
            m.launch(ctx,sdr=True)
            env=popen.call_args.kwargs['env']
            self.assertEqual(env['TOTK_HDR'],'0')
            self.assertNotIn('TOTK_FRAMEGEN',env)
        self.assertTrue((logs/'game-sdr.log').is_file())
        self.assertEqual((logs/'game.log').read_bytes(),b'original failure')
        self.assertEqual(ini.read_bytes(),b'hdr=1\nframegen=fsr3\n')

    def test_cleanup_moves_owned_mod_and_only_extension_settings(self):
        game,ctx=self.fixture();root=game.parent
        mod=root/'mods/PS5_Icons_Manager';mod.mkdir(parents=True)
        (mod/'manifest.tsv').write_bytes(b'owned files')
        m.write_json(mod/'.totk-manager.json',dict(files={'manifest.tsv':m.digest(mod/'manifest.tsv')}))
        other=root/'mods/PlayerMod';other.mkdir()
        ini=root/'totk.ini'
        personal=b'\xef\xbb\xbf[display]\r\nhdr = 1\r\ndlaa = 1\r\nmenu_lang = zh-Hans\r\n'
        before=personal+b'DLSS_MODEL = m\r\ndlss_preset = m\r\nPS5_PROMPTS=1\r\nprompts = "ps5"\r\n'
        ini.write_bytes(before)
        m.main(dict(action='cleanup-owned',game=str(game),cache=str(self.root/'cache')))
        self.assertFalse(mod.exists())
        self.assertTrue(other.is_dir())
        self.assertEqual(ini.read_bytes(),personal)
        backup,=list((m.home(ctx)/'backups').glob('restore-clean-*'))
        self.assertEqual((backup/'totk.ini').read_bytes(),before)
        self.assertEqual((backup/mod.name/'manifest.tsv').read_bytes(),b'owned files')
        m.cleanup_owned(ctx)
        self.assertEqual(list((m.home(ctx)/'backups').glob('restore-clean-*')),[backup])

    def test_diagnostic_records_current_settings_and_file_identity(self):
        game,ctx=self.fixture();(game.parent/'totk.ini').write_text('dlaa=1\nframegen=fsr3\nsecret_token=private\n')
        output=self.root/'diagnostic.zip'
        app=self.root/'app';app.mkdir()
        (app/'manager-version.txt').write_text('1.2.4\n',encoding='ascii')
        with mock.patch.object(m,'APP',app):
            m.main(dict(action='export',game=str(game),cache=str(self.root/'cache'),output=str(output)))
        with zipfile.ZipFile(output) as z:
            status=json.loads(z.read('status.json'))
            self.assertEqual(status['manager_version'],'1.2.4')
            self.assertEqual(status['executable_sha256'],m.digest(game))
            self.assertEqual(status['settings']['FRAMEGEN'],'fsr3')
            self.assertNotIn('SECRET_TOKEN',status['settings'])
            self.assertIn('log-times.json',z.namelist())

    def test_render_cache_reset_preserves_saves_config_and_backup(self):
        game,ctx=self.fixture();root=game.parent;profile=Path(ctx['profile'])
        (root/'shader-cache').mkdir();(root/'shader-cache/a.spv').write_bytes(b'shader')
        (root/'pipeline-cache.bin').write_bytes(b'pipeline')
        (root/'totk.ini').write_text('menu_lang=zh-Hans\nframegen=fsr3\n')
        (profile/'save').mkdir(parents=True);(profile/'save/progress.sav').write_bytes(b'save')
        (profile/'resolution').mkdir();(profile/'resolution/Product.Nin_NX_NVN.HeapDef.byml.zs').write_bytes(b'generated resolution')
        before=(root/'totk.ini').read_bytes();m.reset_render_cache(ctx)
        self.assertFalse((root/'shader-cache').exists());self.assertFalse((root/'pipeline-cache.bin').exists())
        self.assertEqual((profile/'save/progress.sav').read_bytes(),b'save');self.assertEqual((root/'totk.ini').read_bytes(),before)
        backup=next((m.home(ctx)/'backups').glob('render-cache-*'))
        self.assertEqual((backup/'game/shader-cache/a.spv').read_bytes(),b'shader')
        self.assertEqual((backup/'profile-generated/resolution/Product.Nin_NX_NVN.HeapDef.byml.zs').read_bytes(),b'generated resolution')

    def test_render_cache_reset_rolls_back_partial_move(self):
        game,ctx=self.fixture();root=game.parent
        (root/'shader-programs.bin').write_bytes(b'programs');(root/'pipeline-cache.bin').write_bytes(b'pipeline')
        move=m.shutil.move;calls=0
        def fail_second(source,target):
            nonlocal calls
            calls+=1
            if calls==2:raise OSError('locked cache')
            return move(source,target)
        with mock.patch.object(m.shutil,'move',side_effect=fail_second):
            with self.assertRaises(OSError):m.reset_render_cache(ctx)
        self.assertEqual((root/'shader-programs.bin').read_bytes(),b'programs')
        self.assertEqual((root/'pipeline-cache.bin').read_bytes(),b'pipeline')

    def test_renamed_running_game_blocks_mutations(self):
        game,ctx=self.fixture();ctx['game']=str(game.with_name('CustomGame.exe'))
        def listing(args,**kwargs):
            return mock.Mock(stdout=b'"CustomGame.exe","123"' if 'IMAGENAME eq CustomGame.exe' in args else b'INFO: no tasks')
        with mock.patch.object(m.subprocess,'run',side_effect=listing):
            with self.assertRaises(ValueError):self.idle(ctx)

    def test_restore_official_uses_clean_source_mode(self):
        game,ctx=self.fixture()
        with mock.patch.object(m,'release',return_value=({},{})),mock.patch.object(m,'build') as build:
            m.main(dict(action='restore-official',game=str(game),cache=str(self.root/'cache'),cleanup=True))
            self.assertTrue(build.call_args.kwargs['official']);self.assertTrue(build.call_args.kwargs['cleanup'])
            self.assertEqual(build.call_args.args[1:3],(False,False))

    def test_network_routes_and_public_origin_validation(self):
        url=m.API+'/releases/latest'
        self.assertEqual(m.network_routes(url)[0][1],'https://gh-proxy.com/'+url)
        m.NETWORK='direct';self.assertEqual(m.network_routes(url),[('GitHub 直连',url)])
        for url in ['http://github.com/a','https://example.com/a','https://user:secret@github.com/a','https://gh-proxy.com/#https://github.com/a']:
            with self.assertRaises(ValueError):m.network_routes(url)

    def test_network_bad_mirror_json_falls_back(self):
        wanted=dict(tag_name='v0.3.10',assets=[])
        with mock.patch.object(m.urllib.request,'urlopen',side_effect=[io.BytesIO(b'<html>error</html>'),io.BytesIO(json.dumps(wanted).encode())]) as opened:
            self.assertEqual(m.get_json(m.API+'/releases/latest'),wanted)
            self.assertEqual(opened.call_count,2)

    def test_download_bad_mirror_hash_falls_back_without_partial_install(self):
        class Response(io.BytesIO):
            @property
            def headers(self):return {'Content-Length':str(len(self.getvalue()))}
        target=self.root/'runtime.dll';target.write_bytes(b'old')
        data=b'MZ good runtime';sha=hashlib.sha256(data).hexdigest()
        with mock.patch.object(m.urllib.request,'urlopen',side_effect=[Response(b'corrupted'),Response(data)]):
            m.download('https://raw.githubusercontent.com/NVIDIA/DLSS/main/runtime.dll',target,sha)
        self.assertEqual(target.read_bytes(),data)
        with mock.patch.object(m.urllib.request,'urlopen',side_effect=urllib.error.URLError('offline')):
            with self.assertRaises(RuntimeError):m.download('https://github.com/a/b/file',target,'0'*64)
        self.assertEqual(target.read_bytes(),data)

    def test_backup_has_saves_and_no_keys(self):
        game,ctx=self.fixture(); save=Path(ctx['profile'])/'save/a.sav';save.parent.mkdir(parents=True);save.write_bytes(b'my save')
        (Path(ctx['profile'])/'account.uid').write_bytes(bytes(range(16)))
        (game.parent/'prod.keys').write_bytes(b'private')
        p=m.backup_personal(ctx)
        with zipfile.ZipFile(p/'存档与配置.zip') as z:self.assertEqual(z.namelist(),['profile/account.uid','profile/save/a.sav'])

    def test_repacked_standard_romfs_without_metadata(self):
        game,ctx=self.fixture()
        for padding in [0, 8192]:
            dirs=struct.pack('<6I',0,0xffffffff,0xffffffff,0,0xffffffff,0)
            files=struct.pack('<IIQQII',0,0xffffffff,0,4,0xffffffff,5)+b'a.txt'+b'\0'*3
            at=80+len(dirs)+len(files)+padding
            image=struct.pack('<10Q',80,0,0,80,len(dirs),0,0,80+len(dirs),len(files),at)+dirs+files+b'\0'*padding+b'DATA'
            (Path(ctx['romfs'])/'romfs.bin').write_bytes(image)
            self.assertEqual(m.local_resources(ctx),{'a.txt':(at,4)})
            (game.parent/'romfs-index.bin').write_bytes(struct.pack('<8sQQ',b'TOTKRFS1',0,24))
            self.assertEqual(m.local_resources(ctx),{'a.txt':(at,4)})

    def test_concurrent_manager_blocked(self):
        import msvcrt
        game,ctx=self.fixture();p=m.home(ctx)/'operation.lock';p.parent.mkdir(parents=True);p.write_bytes(b'0')
        with p.open('r+b') as f:
            msvcrt.locking(f.fileno(),msvcrt.LK_NBLCK,1)
            with self.assertRaises(ValueError):m.main(dict(action='icons',game=str(game),cache=str(self.root/'cache')))
            f.seek(0);msvcrt.locking(f.fileno(),msvcrt.LK_UNLCK,1)
        self.assertFalse((game.parent/'mods').exists())

    def test_large_flat_directory_table_avoids_recursion_limit(self):
        game,ctx=self.fixture();count=1500;offsets=[];at=24
        for i in range(count):offsets.append(at);at+=24+((len(str(i))+3)//4)*4
        dirs=bytearray(struct.pack('<6I',0,0xffffffff,24,0xffffffff,0xffffffff,0))
        for i in range(count):
            name=str(i).encode();sibling=offsets[i+1] if i+1<count else 0xffffffff
            dirs+=struct.pack('<6I',0,sibling,0xffffffff,0xffffffff,0xffffffff,len(name))+name+b'\0'*((-len(name))%4)
        end=80+len(dirs);image=struct.pack('<10Q',80,0,0,80,len(dirs),0,0,end,0,end)+dirs
        (Path(ctx['romfs'])/'romfs.bin').write_bytes(image)
        self.assertEqual(m.local_resources(ctx),{})

    def test_cancel_preserves_active_pointer(self):
        game,ctx=self.fixture();state={'known':'good'};m.write_json(m.home(ctx)/'active.json',state)
        m.CANCEL=self.root/'cancel';m.CANCEL.write_text('cancel')
        with self.assertRaises(RuntimeError):m.cancelled()
        self.assertEqual(m.active(ctx),state)

    def test_actual_resources_independent_icons_on_arbitrary_host(self):
        # The icons read real guest RomFS bytes, so this test needs a local install. Point
        # TOTK_TEST_ROMFS at a folder holding romfs.bin and TOTK_TEST_INDEX at a TOTKRFS1
        # index (or at a game folder that already has romfs-index.bin beside its exe).
        # Without that data the test is skipped instead of failing.
        romfs=Path(os.environ.get('TOTK_TEST_ROMFS',''))
        index=Path(os.environ.get('TOTK_TEST_INDEX',''))
        app=Path(os.environ.get('TOTK_TEST_APP',''))
        if not (romfs/'romfs.bin').is_file() or not index.is_file() or not (app/'assets.zip').is_file():
            self.skipTest('set TOTK_TEST_ROMFS, TOTK_TEST_INDEX and TOTK_TEST_APP for the private fixture')
        game,ctx=self.fixture('0.99.123+abcdef')
        ctx['romfs']=str(romfs)
        shutil.copy2(index,game.parent/'romfs-index.bin')
        before=game.read_bytes()
        with mock.patch.object(m,'APP',app.resolve()):
            m.install_icons(ctx)
        self.assertEqual(m.icons_health(ctx),'正常');self.assertEqual(game.read_bytes(),before)
        pack=game.parent/'mods/PS5_Icons_Manager';self.assertTrue((pack/'romfs').is_dir())
        self.assertFalse((pack/'romfs/manifest.tsv').exists())
        metadata=json.loads((pack/'.totk-manager.json').read_text(encoding='utf-8'))
        if (pack/'romfs'/m.PS5_ACTION_COMMON).is_file():
            self.assertEqual(metadata['ps5_action_revision'], m.PS5_ACTION_REVISION)
            self.assertFalse(m.ps5_action_outdated(dict(ps5=True, files={m.ICON_FOLDER+'/'+m.PS5_ACTION_COMMON:''},
                                                        ps5_action_revision=metadata['ps5_action_revision'])))
            self.assertTrue(m.ps5_action_outdated(dict(ps5=True, files={m.ICON_FOLDER+'/'+m.PS5_ACTION_COMMON:''})))

    def test_launch_env_probe_strips_mixed_case_steamdeck_without_keyerror(self):
        _, ctx = self.fixture()
        with mock.patch.dict(os.environ, {'SteamDeck': '1', 'SteamOS': '1', 'TOTK_DEVICE_PROFILE': 'deck'}, clear=False):
            env = m.launch_env(ctx, probe=True)
            self.assertFalse(any(k.upper() in ('STEAMDECK', 'STEAMOS') for k in env))
            self.assertEqual(env.get('TOTK_DEVICE_PROFILE'), 'desktop')
            game_env = m.launch_env(ctx, probe=False)
            self.assertTrue(any(k.upper() == 'STEAMDECK' and v == '1' for k, v in game_env.items()))
            self.assertTrue(any(k.upper() == 'STEAMOS' and v == '1' for k, v in game_env.items()))
            self.assertNotIn('TOTK_DEVICE_PROFILE', game_env)

    def test_legacy_prebuilt_recipe_pairing_enforces_exact_manifest_digest(self):
        game, ctx = self.fixture('0.4.0+abcdef')
        app = self.root / 'legacy-pair-app'; payload = app / 'prebuilt'; payload.mkdir(parents=True)
        (app / 'cache/recipes').mkdir(parents=True)
        old_recipe = self.recipe('0.4.0', {'cfg.c': ('a\n', 'a\nold\n')})
        shutil.move(old_recipe, app / 'recipe.zip')
        old_rid = m.recipe_archive_digest(app / 'recipe.zip')
        new_recipe = self.recipe('0.4.0', {'cfg.c': ('a\n', 'a\nnew\n')})
        shutil.move(new_recipe, app / 'cache/recipes/recipe-0.4.0.zip')
        with zipfile.ZipFile(app / 'assets.zip', 'w') as az:
            az.writestr('manifest.json', '[]')
        with mock.patch.object(m, 'APP', app):
            aid = m.assets_id()
        prebuilt_rid = '6' * 64
        manifest_path = payload / 'manifest.json'
        m.write_json(manifest_path, dict(schema=1, version='0.4.0', recipe_id=prebuilt_rid, assets_id=aid, games={'1.4.3': {}}))
        manifest_sha = m.digest(manifest_path)
        m.write_json(m.home(ctx) / 'active.json', dict(mode='direct', version='0.4.0', ps5=True, graphics=False,
                                                         install_method='prebuilt', recipe_id=prebuilt_rid,
                                                         prebuilt_id=manifest_sha, files={'totk.exe': m.digest(game)}))
        pair = {manifest_sha: (prebuilt_rid, frozenset({old_rid}))}
        with mock.patch.object(m, 'APP', app), mock.patch.dict(m._VERIFIED_LEGACY_PREBUILT_PAIRS, pair, clear=True):
            bc = m.bundled_components()
            self.assertEqual(bc['recipe_id'], prebuilt_rid)
            self.assertFalse(m.health(ctx)['recipe_outdated'])
            self.assertEqual(m.recipe_id('0.4.0'), m.recipe_archive_digest(app / 'cache/recipes/recipe-0.4.0.zip'))
            # 篡改 manifest 任意字段使其哈希失效后，完整性检测必须立即拒绝
            m.write_json(manifest_path, dict(schema=1, version='0.4.0', recipe_id=prebuilt_rid, assets_id=aid, games={'1.4.3': {'tampered': True}}))
            with self.assertRaisesRegex(ValueError, '随包程序与修复配方不匹配'):
                m.bundled_components()
            self.assertTrue(m.health(ctx)['recipe_outdated'])


if __name__ == '__main__':
    result=unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.loadTestsFromTestCase(ManagerTests))
    report=dict(status='PASS' if result.wasSuccessful() else 'FAIL',tests=result.testsRun,failures=len(result.failures),errors=len(result.errors),skipped=len(result.skipped),scope='Isolated fixtures and actual local read-only RomFS; no cross-machine or future API guarantee')
    output=ROOT/'build/extension-manager/core-tests.json'
    output.parent.mkdir(parents=True,exist_ok=True)
    output.write_text(json.dumps(report,indent=2),encoding='utf-8')
    raise SystemExit(0 if result.wasSuccessful() else 1)
