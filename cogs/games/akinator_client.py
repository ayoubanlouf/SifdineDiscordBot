import asyncio
import re
from typing import Optional, Dict, Any, Union
from bs4 import BeautifulSoup
from curl_cffi.requests import AsyncSession

class ModernAsyncAkinator:
    """
    Modern Akinator Async API client using curl_cffi to bypass Cloudflare Turnstile bot challenges.
    Provides complete drop-in compatibility with Akinator game flow (start_game, answer, back, exclude, choose, close).
    """
    def __init__(self, lang: str = "en"):
        self.lang = lang
        self.base_url = f"https://{lang}.akinator.com"
        self.session: Optional[AsyncSession] = None
        self.session_token: Optional[str] = None
        self.step: int = 0
        self.progression: float = 0.0
        self.question: Optional[str] = None
        self.first_guess: Optional[Dict[str, Any]] = None
        self.name_proposition: Optional[str] = None
        self.description_proposition: Optional[str] = None
        self.photo: Optional[str] = None
        self.win: bool = False
        self.finished: bool = False
        self.step_last_proposition: str = ""
        self.headers = {
            'x-requested-with': 'XMLHttpRequest',
            'referer': f'{self.base_url}/game',
            'origin': self.base_url
        }

    async def start_game(self) -> str:
        if self.session:
            try:
                await self.session.close()
            except Exception:
                pass
        self.session = AsyncSession(impersonate="chrome120")
        await self.session.get(f"{self.base_url}/")
        await self.session.post(f"{self.base_url}/theme-selection", data={'cm': 'false', 'anim': 'true'})
        r = await self.session.post(f"{self.base_url}/game", data={'sid': '1', 'cm': 'false', 'anim': 'true'})
        
        soup = BeautifulSoup(r.text, 'html.parser')
        m_sess = re.search(r"localStorage\.setItem\('session',\s*'([^']+)'\)", r.text)
        if not m_sess:
            raise RuntimeError("Could not establish Akinator session.")
        self.session_token = m_sess.group(1)
        
        m_step = re.search(r"localStorage\.setItem\('step',\s*'([^']+)'\)", r.text)
        self.step = int(m_step.group(1)) if m_step else 1
        
        q_label = soup.find(id='question-label')
        if not q_label:
            raise RuntimeError("Could not retrieve initial question.")
        self.question = q_label.text.strip()
        self.progression = 0.0
        self.first_guess = None
        self.win = False
        self.finished = False
        return self.question

    async def answer(self, ans: Union[str, int]) -> Union[str, Dict[str, Any]]:
        # Mapping: 0: Yes, 1: No, 2: Don't know, 3: Probably, 4: Probably not
        mapping = {
            "yes": "0", "y": "0", "0": "0", 0: "0",
            "no": "1", "n": "1", "1": "1", 1: "1",
            "i don't know": "2", "idk": "2", "2": "2", 2: "2",
            "probably": "3", "p": "3", "3": "3", 3: "3",
            "probably not": "4", "pn": "4", "4": "4", 4: "4"
        }
        ans_id = mapping.get(str(ans).lower(), "2")
        
        data = {
            'step': str(self.step),
            'progression': str(self.progression),
            'sid': '1',
            'cm': 'false',
            'answer': ans_id,
            'step_last_proposition': self.step_last_proposition,
            'session': self.session_token
        }
        
        r = await self.session.post(f"{self.base_url}/answer", data=data, headers=self.headers)
        res = r.json()
        
        if res.get('completion') == 'KO':
            raise RuntimeError("Akinator returned completion KO.")
            
        if res.get('id_proposition'):
            self.first_guess = {
                'name': res.get('name_proposition'),
                'desc': res.get('description_proposition'),
                'photo': res.get('photo'),
                'id': res.get('id_proposition')
            }
            self.name_proposition = res.get('name_proposition')
            self.description_proposition = res.get('description_proposition')
            self.photo = res.get('photo')
            self.step_last_proposition = str(res.get('step', self.step))
            self.win = True
            return self.first_guess
        else:
            self.first_guess = None
            self.win = False
            self.question = res.get('question')
            self.step = int(res.get('step', self.step + 1))
            self.progression = float(res.get('progression', self.progression))
            return self.question

    async def back(self) -> str:
        data = {
            'step': str(self.step),
            'progression': str(self.progression),
            'sid': '1',
            'cm': 'false',
            'session': self.session_token
        }
        r = await self.session.post(f"{self.base_url}/cancel_answer", data=data, headers=self.headers)
        res = r.json()
        self.question = res.get('question')
        self.step = int(res.get('step', max(1, self.step - 1)))
        self.progression = float(res.get('progression', self.progression))
        self.first_guess = None
        self.win = False
        return self.question

    async def exclude(self, forward_answer: str = "1") -> str:
        data = {
            'step': str(self.step),
            'sid': '1',
            'cm': 'false',
            'progression': str(self.progression),
            'session': self.session_token,
            'forward_answer': forward_answer
        }
        r = await self.session.post(f"{self.base_url}/exclude", data=data, headers=self.headers)
        res = r.json()
        self.first_guess = None
        self.win = False
        self.question = res.get('question')
        self.step = int(res.get('step', self.step + 1))
        self.progression = float(res.get('progression', self.progression))
        return self.question

    async def choose(self):
        """Akinator proposition accepted."""
        self.finished = True

    async def close(self):
        if self.session:
            try:
                await self.session.close()
            except Exception:
                pass
            self.session = None
