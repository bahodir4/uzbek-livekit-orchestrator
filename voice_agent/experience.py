"""Whitelisted UI personas/languages with honest provider limitations."""
from __future__ import annotations
PERSONAS = {
 'john': {'id':'john','name':'John','role':'AI Assistant','voice_gender':'male', 'instructions':'Your name is John. Be professional, clear and concise.'},
 'leila': {'id':'leila','name':'Лейла','role':'ИИ-ассистент','voice_gender':'female','instructions':'Your name is Leyla. Be warm, helpful, accurate, and concise.'},
 'daniyar': {'id':'daniyar','name':'Данияр','role':'ИИ-консультант','voice_gender':'male',
     'instructions':'Your name is Daniyar. Be thoughtful, accurate, friendly, and concise.'},
 'leyla': {'id':'leyla','name':'Лейла','role':'ИИ-ассистент','voice_gender':'female',
     'instructions':'Your name is Leyla. Be warm, helpful, accurate, and concise.'},
 'sofia': {'id':'sofia','name':'София','role':'ИИ-ассистент','voice_gender':'female',
     'instructions':'Your name is Sofia. Be clear, patient, accurate, and concise.'}
}
LANGUAGES = {'uz':'Uzbek','ru':'Russian','en':'English'}
MODELS = {'sayro','navoiy'}

def check_preferences(persona:str,language:str,model:str):
 if persona not in PERSONAS: raise ValueError('Unknown persona')
 if language not in LANGUAGES: raise ValueError('Unknown language')
 if model not in MODELS: raise ValueError('Unknown TTS model')

def agent_instructions(persona:str,language:str,original:str)->str:
 check_preferences(persona,language,'sayro')
 locale = 'Reply in Uzbek (Latin script) by default.' if language=='uz' else ('Reply in Russian by default.' if language=='ru' else 'Reply in English by default.')
 return '\n'.join((original, PERSONAS[persona]['instructions'], locale,
   'Do not invent facts, capabilities, phone actions, or outside knowledge.'))
