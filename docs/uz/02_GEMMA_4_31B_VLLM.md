# 02 — vLLM orqali Gemma 4 31B IT (RunPod, Dockersiz)

**Model:** `google/gemma-4-31B-it`, to'liq BF16 (bazaviy `google/gemma-4-31B` emas). **Uskuna:** rasmiy minimal NVIDIA BF16 retsepti bo'yicha 1× A100 80GB; ko'proq zaxira uchun tensor parallelligi 2 bo'lgan 2× A100 80GB. Model fayllari taxminan **62,6 GB**, shuning uchun bu Pod uchun kamida ~100 GB doimiy disk ajrating. 8K token va o'rtacha parallellik bilan boshlang.

## A. GPU muhiti va o'rnatish

Mos drayverli RunPod CUDA/PyTorch Pod va **Python 3.12** ishlating. GPU va diskni tekshiring:

```bash
nvidia-smi
python3.12 --version
df -h /workspace
```

Python 3.12 uchun:

```bash
python3.12 -m venv /workspace/gemma-vllm-venv
source /workspace/gemma-vllm-venv/bin/activate
python -m pip install -U pip uv
uv pip install vllm --torch-backend=auto
vllm --version
```

Shabloningizda Python 3.12 bo'lmasa, Torch/CUDA paketlarini majburan yangilash o'rniga vLLM qo'llab-quvvatlaydigan Python versiyasi / mos RunPod shablonidan foydalaning. vLLM'ning GPU o'rnatish qo'llanmasini ko'ring. Yopiq (gated) kirish / tezlik cheklovlari uchun `hf auth login` orqali kiring (kerak bo'lsa `huggingface_hub[cli]` ni o'rnating) yoki `HF_TOKEN` ni Pod muhitida xavfsiz bering.

## B. Serverni ishga tushirish

Kalit vLLM'ga **`VLLM_API_KEY` muhit o'zgaruvchisi** orqali beriladi. Buyruq satrida `--api-key` ishlatmang: u jarayonlar ro'yxatida (`ps`, `pgrep -af`) ko'rinib qoladi. Kalit yarating va uni Git hamda loglardan uzoq tuting:

```bash
python3 -c 'import secrets; print(secrets.token_urlsafe(32))'
```

Eski vLLM GPU'ni ushlab turmaganiga ishonch hosil qiling (aks holda ishga tushirish `Free memory on device ... is less than desired GPU memory utilization` xatosi bilan tugaydi):

```bash
pkill -9 -f vllm; pkill -9 -f EngineCore; sleep 5
nvidia-smi --query-gpu=memory.used --format=csv      # taxminan 0 MiB bo'lishi kerak
```

Ishga tushirish skriptini bir marta yarating. **Tool-calling flaglari majburiy**: agentlarda vositalar (kredit/omonat kalkulyatorlari, band qilish) bor, shuning uchun orchestrator `tool_choice: auto` yuboradi. Bu flaglarsiz har bir javob `400 "auto" tool choice requires --enable-auto-tool-choice and --tool-call-parser to be set` xatosi bilan tugaydi.

```bash
cat > /workspace/start_llm.sh <<'EOF'
#!/usr/bin/env bash
source /workspace/gemma-vllm-venv/bin/activate
export HF_HOME=/workspace/gemma-hf-cache
: "${VLLM_API_KEY:?set VLLM_API_KEY first}"
exec vllm serve google/gemma-4-31B-it --host 0.0.0.0 --port 8000 \
  --dtype bfloat16 --max-model-len 8192 --gpu-memory-utilization 0.90 \
  --enable-auto-tool-choice --tool-call-parser gemma4 --reasoning-parser gemma4
EOF
chmod +x /workspace/start_llm.sh
```

Fon rejimida ishga tushiring (kalit so'rovi yashirin; o'rinbosar so'z emas, kalitning o'zini kiriting):

```bash
read -rs -p "VLLM_API_KEY: " VLLM_API_KEY; export VLLM_API_KEY; echo
nohup /workspace/start_llm.sh > /workspace/vllm.log 2>&1 &
tail -f /workspace/vllm.log          # Ctrl+C kuzatishni to'xtatadi, serverni emas
```

`Application startup complete` yozuvini kuting (birinchi marta bir necha daqiqa; 62 GB yuklab olish `/workspace/gemma-hf-cache` da keshlanadi). `vllm` faqat venv ichidagi PATH'da bor, shuning uchun yangi qobiqda oddiy `vllm serve` `command not found` xatosini beradi; skriptdan foydalaning.

Bu **bitta** A100 80GB ishlatadi. **Ikkita** A100 uchun `--tensor-parallel-size 2` qo'shing. `--max-model-len` va GPU xotira sozlamalari boshlang'ich qiymatlar, kafolatlangan tezlik emas. `--host 0.0.0.0` RunPod HTTP proksisi orqali kirish uchun kerak, shuning uchun doim autentifikatsiya va TLS'ni talab qiling.

vLLM versiyangiz qaysi parser nomlarini qabul qilishini tekshiring: `/workspace/gemma-vllm-venv/bin/vllm serve --help=all | grep -A2 -- --tool-call-parser`. vLLM 0.32.0 da tekshirilgan: u `Gemma4ForConditionalGeneration` arxitekturasini yuklaydi; yuqoridagi tool-call flaglari **hali boshidan oxirigacha tekshirilmagan** (D bo'limiga qarang).

## C. Tayyorlikni va oqimli chiqishni tekshirish

LLM Podning ikkinchi terminalida, xuddi shu kalit xavfsiz mavjud holda:

```bash
curl -fsS http://127.0.0.1:8000/v1/models \
  -H "Authorization: Bearer $VLLM_API_KEY"
```

Model ID JSON'dagi qiymatga mos kelishi kerak. **Oqimli matn**ni sinang:

```bash
curl -N --fail-with-body -sS http://127.0.0.1:8000/v1/chat/completions \
  -H "Authorization: Bearer $VLLM_API_KEY" \
  -H 'Content-Type: application/json' \
  -d '{
    "model":"google/gemma-4-31B-it",
    "messages":[{"role":"user","content":"O‘zbek tilida bir gap bilan salomlash."}],
    "temperature":0.3,
    "max_tokens":90,
    "stream":true
  }'
```

Muvaffaqiyatli SSE javoblari `data: ...` hodisalari sifatida bosqichma-bosqich keladi. Masofaviy orchestrator bazaviy manzili: `https://LLM_POD_ID-8000.proxy.runpod.net/v1` (`8000/http` ni oching). Aynan shu xizmat ko'rsatadigan model nomini `config/agent.yaml` ga yozing va orchestrator `.env` faylida **bir xil** `VLLM_API_KEY` dan foydalaning.

## D. Vositalar va unumdorlik

- **Tool chaqiruvlari:** ixtisoslashgan agentlar uchun majburiy (`06_AGENTS.md` ga qarang). vLLM'ni `--enable-auto-tool-choice --tool-call-parser gemma4 --reasoning-parser gemma4` bilan ishga tushiring (B bo'limi). vLLM versiyangiz retseptdagi `tool_chat_template_gemma4.jinja` ni ham talab qilsa, `--chat-template <yo'l>` qo'shing. Amallarga tayanishdan oldin tuzilmali tool chaqiruvini boshidan oxirigacha sinang (masalan, omonat agentidan omonat hisobini so'rang). vLLM'ni bu flaglarsiz ishga tushirsangiz, worker logida har bir javobda `400 "auto" tool choice requires --enable-auto-tool-choice ...` ko'rinadi.
- **Kechikish:** Ovozli qo'ng'iroqlar uchun javob uzunligini kamaytiring (orchestrator'dagi `max_completion_tokens`, masalan 128–256), birinchi token kechikishini sinang va uzun fikrlash (reasoning) chiqishlaridan qoching. Bir vaqtdagi qo'ng'iroqlarni oshirishdan oldin KV-kesh ishlatilishi va modelning xotira hajmini tekshiring.
- **OOM (xotira yetmasligi):** Kontekst/oyna va bir vaqtdagi so'rovlarni kamaytiring yoki ikkita 80GB GPU'ga o'ting; 24 GB GPU'lar kvantlanmagan 31B BF16 vaznlarini ko'p-GPU bo'lishsiz/offload'siz sig'dira olmaydi.
- **401:** Agent va server **bir xil** yangi API kalitidan foydalanishi kerak; `.env` qiymatida `Bearer` bo'lmasligi kerak.
- **RunPod manzilidan 502:** proksi vLLM'ga yeta olmayapti: u hali yuklanmoqda, to'xtagan yoki manzil boshqa podga tegishli. Podda `pgrep -af "vllm serve"` va `tail -n 30 /workspace/vllm.log` ni tekshiring; yangi pod yaratganingizda pod ID o'zgaradi.
- **`Free memory on device (18.59/79.25 GiB) ... less than desired GPU memory utilization`:** oldingi vLLM jarayoni hali GPU'ni ushlab turibdi. Uni o'chiring (B bo'limi) yoki podni qayta ishga tushiring.

**Rasmiy hujjatlar:** https://docs.vllm.ai/projects/recipes/en/stable/Google/Gemma4.html · https://docs.vllm.ai/en/stable/getting_started/quickstart/ · https://huggingface.co/google/gemma-4-31B-it

## E. Ovozli kechikish uchun sozlash (birinchi token ~1 s dan kam)

RunPod HTTPS proksisi orqali o'lchandi: birinchi token 0,8–1,8 s, shundan ~0,5–1 s proksi qo'shimchasi.
Avval joylashuvni to'g'rilang (worker'ni bir xil datacenter'da ishga tushiring va podning ichki/localhost manzilidan foydalaning),
keyin serverni sozlang:

- Prefiks keshini yoqilgan holda qoldiring (vLLM V1 da standart; tizim prompti va tarix har bir navbatda qayta ishlatiladi).
- Kontekstni ovozli qo'ng'iroqqa kerak bo'lgan darajagacha pasaytiring: `--max-model-len 4096` (kichikroq KV-kesh, ko'proq zaxira).
- Parallellikni xizmat ko'rsatadigan darajada cheklang: `--max-num-seqs 8` (ovozli qo'ng'iroqlar kam va kechikishga sezgir).
- Dekodlash tezligi xotira o'tkazuvchanligi bilan cheklangan (har bir token uchun ~62 GB BF16 vazn). Yagona katta
  yo'l — har bir token uchun kamroq bayt: faqat-vazn FP8 (`--quantization fp8`) yoki kichikroq instruct model.
  Har qanday o'zgarishdan keyin o'zbekcha javob sifatini tekshiring; yuqoridagi curl sinovidan foydalaning.
- Orchestrator'da javoblarni qisqa tuting (`max_completion_tokens: 200`) va ovoz uchun fikrlash/reasoning
  chiqishini o'chirib qo'ying.

Orchestrator log qatori bilan tekshiring: `Voice metric ... stage=llm {'ttft': ...}` (maqsad < 1 s).
