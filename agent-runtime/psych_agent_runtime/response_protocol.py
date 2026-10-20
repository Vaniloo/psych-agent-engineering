"""Keep native tool protocol out of visible text without executing model-written calls."""

import re


class InternalToolProtocol(ValueError):
    pass


class TextProtocolGuard:
    """Only hold a possible tag prefix; normal text streams without a fixed delay."""

    MARKERS = ('<DSML', '</DSML', '<TOOL_CALL', '</TOOL_CALL',
               '<FUNCTION_CALL', '</FUNCTION_CALL', '<INVOKE', '</INVOKE')

    def __init__(self):
        self.pending = ''

    def feed(self, text):
        self.pending += text
        for match in re.finditer('<', self.pending):
            tail = self.pending[match.start():]
            compact = re.sub(r'[\s|｜]', '', tail[:200]).upper()
            if any(compact.startswith(marker) for marker in self.MARKERS):
                raise InternalToolProtocol('model emitted internal tool protocol')
            if any(marker.startswith(compact) for marker in self.MARKERS):
                if len(tail) > 180:
                    raise InternalToolProtocol('unterminated tool protocol prefix')
                safe, self.pending = self.pending[:match.start()], tail
                return safe
        safe, self.pending = self.pending, ''
        return safe

    def finish(self):
        if len(self.pending.strip()) > 1:
            raise InternalToolProtocol('incomplete tool protocol prefix')
        safe, self.pending = self.pending, ''
        return safe


def contains_protocol(text):
    guard = TextProtocolGuard()
    try:
        guard.feed(text)
        guard.finish()
        return False
    except InternalToolProtocol:
        return True
