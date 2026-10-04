"""Fixed local configured-validator proof fixture. This is not a planner or engine validator. Only the trusted CLI appends one
existing Markdown path to its configured argv. No dependencies or network.
"""
from pathlib import Path
import sys


def main():
    if len(sys.argv) != 2:
        print('CP4 validator invalid invocation')
        return 2
    try:
        with Path(sys.argv[1]).open('rb') as stream:
            raw = stream.read(1024 * 1024 + 1)
        if len(raw) > 1024 * 1024:
            print('CP4 validator size refused')
            return 2
        content = raw.decode('utf-8')
    except (OSError, UnicodeError):
        print('CP4 validator input refused')
        return 2
    if 'REJECT_CP4' in content:
        print('CP4 validator rejected REJECT_CP4')
        return 9
    print('CP4 validator accepted')
    return 0


if __name__ == '__main__':
    sys.exit(main())
