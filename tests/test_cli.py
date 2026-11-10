"""Help, version, the man page and the completion scripts."""

import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from notifyd import __version__
from notifyd.cli import build_parser, main, subparser
from notifyd.completion import all_nodes, script, walk

ROOT = Path(__file__).resolve().parent.parent
MAN = ROOT / 'share' / 'man' / 'man1' / 'notifyd.1'
NOTIFYD = [sys.executable, str(ROOT / 'bin' / 'notifyd')]


def commands():
    return [node.path[1:] for node in all_nodes(walk(build_parser())) if len(node.path) > 1]


def test_version_flags_and_command(capsys):
    for argv in (['--version'], ['-V']):
        with pytest.raises(SystemExit) as exit_info:
            main(argv)
        assert exit_info.value.code == 0
        assert capsys.readouterr().out.strip() == f'notifyd {__version__}'
    assert main(['version']) == 0
    assert capsys.readouterr().out.strip() == f'notifyd {__version__}'


@pytest.mark.parametrize('path', commands(), ids=' '.join)
def test_every_command_has_help(path, capsys):
    flag = '--help'   # send keeps -h for --hint
    with pytest.raises(SystemExit) as exit_info:
        build_parser().parse_args([*path, flag])
    assert exit_info.value.code == 0
    out = capsys.readouterr().out
    assert out.startswith('usage: notifyd ' + ' '.join(path))
    if path != ['send']:
        with pytest.raises(SystemExit):
            build_parser().parse_args([*path, '-h'])


def test_send_help_through_main():
    result = subprocess.run([*NOTIFYD, 'send', '--help'], capture_output=True, text=True, check=False)
    assert result.returncode == 0
    assert '-h, --hint TYPE:NAME:VALUE' in result.stdout and 'examples:' in result.stdout


def test_every_option_has_help_text():
    for node in all_nodes(walk(build_parser())):
        for option in node.options:
            assert option.help, f'{" ".join(node.path)} {option.flags}'
        for positional in node.positionals:
            assert positional.help, f'{" ".join(node.path)} {positional.name}'


def test_help_command(capsys):
    assert main(['help', 'ctl', 'history']) == 0
    assert capsys.readouterr().out.startswith('usage: notifyd ctl history')
    assert main(['help']) == 0
    assert 'commands:' in capsys.readouterr().out
    assert main(['help', 'nope']) == 2
    assert subparser(build_parser(), ['ctl', 'nope']) is None


# -- man page ---------------------------------------------------------------------

def man_text():
    return MAN.read_text().replace('\\-', '-')


def test_man_page_version_matches():
    assert f'"notifyd {__version__}"' in MAN.read_text().splitlines()[0]


def test_man_page_names_every_command_and_option():
    text = man_text()
    missing = []
    for node in all_nodes(walk(build_parser())):
        for name in node.path[1:]:
            if not re.search(rf'(?<![\w-]){re.escape(name)}(?![\w-])', text):
                missing.append(name)
        for option in node.options:
            for flag in option.flags:
                if flag not in ('-h', '--help') and flag not in text:
                    missing.append(f'{" ".join(node.path)} {flag}')
    assert missing == []


@pytest.mark.skipif(not shutil.which('groff'), reason='needs groff')
def test_man_page_renders_without_warnings():
    result = subprocess.run(['groff', '-man', '-ww', '-z', str(MAN)], capture_output=True, text=True, check=False)
    assert result.returncode == 0 and result.stderr == ''


# -- completions --------------------------------------------------------------------

@pytest.mark.parametrize('shell, checker', [('bash', ['bash', '-n']), ('zsh', ['zsh', '-n']),
                                            ('fish', ['fish', '--no-execute'])])
def test_completion_scripts_parse(shell, checker, tmp_path):
    if not shutil.which(checker[0]):
        pytest.skip(f'needs {checker[0]}')
    path = tmp_path / f'notifyd.{shell}'
    path.write_text(script(shell, build_parser()))
    result = subprocess.run([*checker, str(path)], capture_output=True, text=True, check=False)
    assert result.returncode == 0, result.stderr


def test_completion_command(capsys):
    assert main(['completion', 'zsh']) == 0
    assert capsys.readouterr().out.startswith('#compdef notifyd')


def bash_complete(tmp_path, *words):
    path = tmp_path / 'c.bash'
    path.write_text(script('bash', build_parser()))
    quoted = ' '.join(f"'{w}'" for w in words)
    code = (f'source {path}; COMP_WORDS=({quoted}); COMP_CWORD=$((${{#COMP_WORDS[@]}}-1)); _notifyd; '
            'printf "%s\\n" "${COMPREPLY[@]}"')
    env = {'PATH': f'{ROOT / "bin"}:/usr/bin:/bin', 'HOME': str(tmp_path)}
    result = subprocess.run(['bash', '-c', code], capture_output=True, text=True, env=env, check=False, cwd=tmp_path)
    assert result.returncode == 0, result.stderr
    return result.stdout.split()


@pytest.mark.skipif(not shutil.which('bash'), reason='needs bash')
@pytest.mark.parametrize('words, expected', [
    (['notifyd', ''], ['daemon', 'send', 'ctl', 'presets', 'import', 'check', 'rules', 'completion', 'help',
                       'version']),
    (['notifyd', 'ctl', 'pr'], ['preset', 'preview']),
    (['notifyd', 'ctl', 'preset', 'de'], ['deadd']),
    (['notifyd', 'daemon', '--renderer', ''], ['gtk', 'none']),
    (['notifyd', 'send', '-u', 'c'], ['critical']),
    (['notifyd', 'send', '--prog'], ['--progress']),
    (['notifyd', 'completion', ''], ['zsh', 'bash', 'fish']),
    (['notifyd', 'help', 'c'], ['ctl', 'check', 'completion']),
    (['notifyd', 'ctl', 'history', '-n', '5', '--'], ['--help', '--json']),
])
def test_bash_completion(tmp_path, words, expected):
    assert bash_complete(tmp_path, *words) == expected


@pytest.mark.skipif(not shutil.which('bash'), reason='needs bash')
def test_bash_completes_files_for_paths(tmp_path):
    (tmp_path / 'deadd.yml').write_text('')
    assert 'deadd.yml' in bash_complete(tmp_path, 'notifyd', 'import', 'deadd', '--yml', 'dea')


def test_zsh_script_shape():
    text = script('zsh', build_parser())
    assert "'(-c --config)'{-c,--config}'[config file (default\\: ~/.config/notifyd/config.yaml)]" not in text
    assert "{-c,--config}'[config file (default: ~/.config/notifyd/config.yaml)]:path:_files'" in text
    assert "'*'{-h,--hint}" in text                     # repeatable, no exclusion list
    assert '_notifyd_cmd_notifyd_ctl_preset' in text
    assert ":name:_notifyd_presets'" in text
    assert "'--renderer[gtk draws popups; none only logs (default\\: gtk)]" not in text


def test_fish_script_shape():
    text = script('fish', build_parser())
    assert "complete -c notifyd -n '__notifyd_is \"notifyd\"' -a ctl -d 'control the running daemon'" in text
    assert "-l renderer -x -a 'gtk none'" in text
    assert "-n '__notifyd_is \"notifyd ctl preset\"' -x -a '(notifyd presets --names 2>/dev/null)'" in text
    assert "-s c -l config -r -F" in text
