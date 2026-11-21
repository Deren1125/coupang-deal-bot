# 취준 탭 — 만들 계획 (기획: Fable · 만들기: Opus · 검토: Fable)

2026-11-22. 작업 폴더 `/home/ubuntu/wt-jobprep` (가지 `jobprep`). `/home/ubuntu/coupang-deal-bot` 은 돌아가는 서버 — 건드리지 않는다.
주인이 이미 정한 것(탭 9개·순서·출처 원칙·상한)은 다시 묻지 않는다. 이 글은 "어떻게 만들까"만 적는다.

## 0. 먼저 알아 둘 것 (조사 결과)

- 대시보드는 자체 웹 서버(`dealbot/web.py`) + 해시 화면 앱(`static/app.js`). 아래 탭은 `icons.TABS` 6개 + 파밍 링크 1개(`shell._tabs`, CSS `.tabs` 가 `repeat(7,…)`).
- 로그인: `/dashboard` 앞부분 전체에 `guard_prefix`. 화면 주소는 `service.PAGE_PATHS / PAGE_PREFIXES` 에 있어야 로그인 화면으로 넘어간다. POST 는 `api.guard(headers)` (같은 사이트 + `X-Dash-Token`).
- 정적 파일: `api.APP_FILES` 에 이름을 넣으면 `/dashboard/static/<이름>` 으로 열린다 (로그인 없이 — **데이터를 넣으면 안 됨**). 주소 뒤 `?v=해시` 는 `shell.asset_url`.
- 주기 일: `scheduler.dashboard_loop` 가 1분마다 각 모듈 `tick()` 을 부른다 (xcap 방식: 정한 시각이 지났으면 뒤에서 하나, 도는 중이면 건너뜀, 마지막 실행은 kv 에).
- AI 호출: `dealbot/api_credit.py` — Claude API `ask()` · 장부 `~/.local/share/api-credit/YYYY-MM.jsonl` (`record`, `totals`) · Gemini 는 헤더 `x-goog-api-key`. **이 파일은 메인 6 이 고치는 중 — 읽어 쓰기만 하고 고치지 않는다.**
- 서버: 메모리 3.8GB (여유 1.7GB · 스왑 1GB 쓰는 중) · CPU 2개 · 디스크 25GB 남음. `sudo -n` 됨. `unshare -rn` 은 막혀 있음. ffmpeg 있음. pypdf·espeak 없음.
- 확인한 공개 주소 (11/22, 로그인 없이 200): Greenhouse `anthropic·coinbase·okx·daangn·jumptrading·flowtraders·stripe·databricks` · Lever `binance·wintermute-trading` · Ashby `openai(14MB)·kraken.com·perplexity·cursor` · DefiLlama fees(5.5MB) · yc-oss · 링커리어·사람인·캐치 robots.txt. 404: greenhouse `toss·dunamu·upstage·rebellions·wintermute`, ashby `hyperliquid·lighter` → 회사 채용 페이지 직접(지켜보는 회사). 자소설닷컴은 서버에서 403.
- CtCI PDF: 글자 뽑힘(스캔 글자라 숫자가 가끔 깨짐). 1권 목차(outline)에 장별 쪽 있음. 풀이는 `N.M 제목: … pgNNN SOLUTION` 꼴 (1권 67개 + 2권 125개 `SOLUTION` 표시).

## 1. 파일 목록

### 새 파일 (모두 메인 담당 · 다른 세션 파일 안 건드림)
```
src/dealbot/dashboard/jobprep/
  __init__.py      register(web, api) → Jobprep 객체 (경로 등록 · tick)
  store.py         sqlite (jobprep-data/jobprep.db) · 표 만들기 · kv · 파일 경로 (data_dir 밖으로 못 나가게)
  llm.py           Gemini·Claude 부르기 + 취준 몫 상한 + 캐시(llm_cache) — 키 값은 읽어서 헤더에만
  page.py          /dashboard/jobprep/ 화면 한 장(HTML 틀) + 취준 전용 CSP
  chat.py          아래 대화창 (탭별 맥락 만들기 · 기록)
  today.py         ⓪ 오늘
  profile.py       ① 내 소개 (프로필 = 적합도·비교의 기준)
  academic.py      ② 학사·졸업 (올리기·읽기·남은 학점·수강 계획·재수강 계산)
  jobs.py          ③ 공고 (상태·보드·닫힘·제외 목록·지켜보는 회사)
  collectors.py    ③ 출처별 읽기 (greenhouse·lever·ashby·workable·smartrecruiters·html·defillama·yc)
  companies.py     ③ 회사·분야 씨앗 목록 (코드에 박은 표)
  cases.py         ④ 합격 사례
  ctci.py          ⑤ 문제 목록·사다리·간격 복습·주간 몫
  sandbox.py       ⑤ 코드 돌리기 (격리) + runner 글
  library.py       ⑥ 학습 자료함
  speech.py        ⑦ 스피치 카드·인문학
  english.py       ⑧ 통화 프롬프트·통화 뒤 정리·리얼클래스 표현
  pron.py          ⑧ 발음 채점 (하위 프로세스) · audio.py 를 씀
  audio.py         ffmpeg 로 16k 변환 · 쉼 재기 (⑦⑧ 공용)
  pron_worker.py   발음 채점 일꾼 (python -m … 로 따로 뜸 — onnxruntime·numpy 는 여기서만 import)
src/dealbot/dashboard/static/
  jobprep.js  (틀: 탭 줄·해시 이동·대화창·⓪ 오늘·공용 그리기)   jobprep.css
  jp_jobs.js (③④)  jp_code.js (⑤)  jp_talk.js (⑦⑧ 녹음 포함)  jp_misc.js (①②⑥⑨)   ← 그 탭을 처음 열 때만 받음
tools/jobprep_ctci_extract.py   CtCI 목록 뽑기 (한 번 · 손으로)
tools/jobprep_setup.py          발음 모델·CMUdict 받기 (한 번 · nice 19)
tests/test_jobprep_*.py         6장 참고
docs/jobprep_plan.md (이 글) · docs/DASHBOARD.md 에 '취준' 절 추가
```

### 함께 쓰는 파일에 넣는 줄 (최소)
| 파일 | 넣는 것 |
|---|---|
| `dashboard/icons.py` | `TAB_JOBPREP = _tab('…')` 한 줄 (서류 가방 그림) |
| `dashboard/shell.py` | `JOBPREP_URL = "/dashboard/jobprep/"` + `_tabs()` 끝에 링크 한 줄 (파밍 줄과 같은 꼴, `data-tab` 없음) |
| `static/app.css` | `.tabs` 의 `repeat(7,` → `repeat(8,` (390px 에서 한 칸 48px — 글자 2자 '취준' 확인) |
| `dashboard/api.py` | `APP_FILES` 에 js 5개·css 1개 · `register()` 끝에 `from dealbot.dashboard import jobprep; self.jobprep = jobprep.register(web, self)` |
| `dashboard/service.py` | `PAGE_PATHS` 에 `"/dashboard/jobprep"` · `PAGE_PREFIXES` 에 `"/dashboard/jobprep/"` |
| `scheduler.py` | `dashboard_loop` 에 xcap 과 같은 try 묶음 하나 (`api.jobprep.tick()`) |
| `config.py` | `class JobprepConfig` + `DashboardConfig.jobprep` 한 줄 |
| `pyproject.toml` | `pypdf>=4`, `numpy>=1.26`, `onnxruntime>=1.17`, `yt-dlp` (auto-update 가 pyproject 가 바뀌면 `pip install -e .`) |
| `docs/devlog.md` | 한 줄 |

`sw.js`·`app.js`·`state.py`·`api_credit.py` 는 고치지 않는다 (서비스 워커는 `/dashboard/` 첫 화면만 가로채고 static 은 그대로 다룸).

### 데이터 폴더 `/home/ubuntu/jobprep-data/` (git 밖 · 폴더 700 · 파일 600)
```
jobprep.db                 모든 표 (WAL)
ctci/*.pdf                 책 (이미 있음)       ctci/index.json  문제 목록       ctci/overrides.json  손으로 고친 줄
ctci/text/<qid>.json       문제 글·풀이 글 (책 내용 — 로그인 아래 API 로만, 캐시 금지 no-store)
models/w2v_espeak_int8.onnx (318MB) · models/vocab.json · models/cmudict.dict
acad/<날짜>_<n>.jpg        성적표·자가진단 화면      acad/yoram_*.pdf  요람 (공개 PDF 를 찾았을 때)
audio/pron/ · audio/speech/ · audio/human/     내 녹음 (webm/mp4 원본 · 90일 뒤 지움, 점수는 남김)
cache/http/                공고 출처 응답 (ETag·받은 시각)       tmp/   올리기 임시 · 샌드박스 임시
```
폴더 위치는 설정 `dashboard.jobprep.data_dir` (기본 `~/jobprep-data`, 환경변수 `JOBPREP_DATA_DIR` 가 이김). 테스트는 tmp_path.

### 설정 `JobprepConfig` (모두 설정값 — 코드에 박지 않음)
`enabled` · `data_dir` · `collect_time "06:00"` · `recheck_times ["06:00","18:00"]` · `claude_monthly_usd 40` · `claude_daily_usd 3` ·
`claude_first_week_daily_usd 1` · `claude_first_week_until "2026-12-01"` · `claude_model "sonnet"` · `gemini_fast "gemini-2.5-flash-lite"` ·
`gemini_good "gemini-2.5-flash"` · `gemini_calls_per_day {fast: 400, good: 60}` · `gemini_monthly_usd 3` · `gemini_key_env "JOBPREP_GEMINI_API_KEY"` ·
`discharge "2027-07-13"` · `book_finish_by "2027-08-31"` · `sandbox_mode "auto"` · `pron_min_free_mb 1000` · `fetch_gap_s 3` · `fetch_max_bytes 20MB` · `use_proxy false`

## 2. 공통 틀

- **화면**: `/dashboard/jobprep/` 는 따로 된 화면 한 장 (파밍처럼 아래 탭에서 넘어감 · 맨 위 '← 대시보드'). 세로 한 줄(폰). 맨 위에 가로로 미는 탭 줄
  `오늘·내 소개·학사·공고·합격 사례·코딩·자료함·스피치·영어·설정` = `#/today #/me #/acad #/jobs #/cases #/code #/lib #/speech #/eng #/set`. 마지막 탭은 localStorage.
- **보안 머리글**: `service.SECURITY_HEADERS` 그대로 + 취준 화면만 `media-src 'self' blob:` (녹음 다시 듣기). 페이지 안 스크립트 없음 (boot 는 `application/json`: csrf·설정 요약만).
- **API**: 모두 `/dashboard/api/jobprep/…` (로그인 아래). GET 은 `RateWindow`, POST 는 `route_json` + `api.guard`. 답은 `Cache-Control: no-store`.
  큰 것 올리기는 `route_stream` 하나: `POST …/upload` (머리글 `x-jp-kind: acad|pron|speech|human`, 사진 8MB·소리 3MB).
  파일 보기 `GET …/file/<kind>/<id>` (data_dir 안, 이름은 서버가 붙인 id 만).
- **저장**: 딜봇 DB 와 따로 `jobprep.db` (돌아가는 DB 스키마를 안 건드림). 공통 표: `kv(k,v)` · `llm_cache(key,kind,model,made_at,body)` · `usage(day,kind,n,usd)` · `chat(id,tab,ctx,role,model,text,at)`.
- **주기 일 `Jobprep.tick()`** (1분마다 불림 · 한 번에 뒤 작업 1개 · 여유 메모리 400MB 아래면 미룸 · 마지막 실행을 kv 에 — 재시작해도 두 번 안 돎 · 3시간 안 놓친 건 따라잡음):

| 때 (KST) | 일 | AI |
|---|---|---|
| 매일 06:00 | 공고 모으기 → 있는 공고 다시 확인 → 지켜보는 회사 페이지 바뀜 확인 | 없음 |
| 매일 06:20 | 새 공고 뽑기·적합도 (10개씩 묶어) | Gemini fast |
| 매일 18:00 | 있는 공고 다시 확인만 | 없음 |
| 매일 03:30 | 다음 CtCI 3문제 사다리 미리 만들기 (없을 때만) | Gemini good |
| 월 05:30 | 영어 일주일 상대·상황 7개(1번) · 스피치 카드 3장 · 인문학 7주제(1번) · 약한 소리 연습 문장 | Gemini good 3~5번 |
| 월 05:50 | DefiLlama·거래소 거래량·YC 목록 새로 · 합격 사례 모으기(≤30쪽) | Gemini fast |
| 매일 04:00 | 90일 지난 녹음·임시 파일·오래된 http 캐시 지우기 | 없음 |

- **AI 나눔 (`llm.py`)**
  - Gemini: 키는 `JOBPREP_GEMINI_API_KEY`(무료 키) → 없으면 서버 공용 `GEMINI_API_KEY`(딜봇 환경 → Blog-Auto .env 의 그 한 줄, `ssalmuk.BlogKeyFile` 방식). 헤더로만. JSON 답은 `responseSchema`.
    쓴 양은 `api_credit.record("jobprep.<일>", model, nums, prov="gemini")` + 취준 표 `usage`. 하루 횟수 상한·달 추정액 상한을 넘으면 그날 뒤 작업을 쉬고 화면에 한 줄.
  - Claude: '클로드에게 묻기' 단추에서만. `api_credit.allow()` 통과 + 취준 몫(장부에서 `job` 이 `jobprep.` 로 시작하는 줄 합계: 오늘 < `claude_daily_usd`(첫 주는 1) · 이번 달 < 40) 일 때만
    `api_credit.ask("jobprep.chat", api_credit.api_model(cfg.claude_model, posted=False), system, user, effort="low", max_tokens=1500)`.
    넘으면 부르지 않고 `429 {code:"cap"}` — "오늘 Claude 몫을 다 썼어요 · Gemini 로 물을 수 있어요". **구독 CLI 로 넘어가지 않는다.** 키 오류면 `mark_failed` 는 api_credit 이 함.
  - 캐시: `key = sha256(kind + 모델 + 프롬프트 판 번호 + 입력)` — 있으면 다시 안 부름. 개념 설명·몸풀기·시험·주간 묶음·적합도 모두 여기.
  - 프롬프트·답 글은 로그에 안 남김 (해시 8자·글자 수·모델·상태만).
- **아래 대화창 (모든 탭)**: 화면 아래 고정 입력줄 → 누르면 메신저 꼴 시트. 보내기 = Gemini(기본) · 옆 단추 '클로드에게 묻기'. 머리에 `오늘 $0.42/$3 · 이번 달 $5.10/$40`.
  `POST …/chat {tab, ctx, text, model}` → `chat.context(tab, ctx)` 가 그 탭에 보이는 것을 짧게 묶음(≤6천 토큰: 프로필 요약 + 탭별 — 공고: 보이는 카드 20개 요약 · 코딩: 문제·내 코드·마지막 채점 · 영어: 오늘 프롬프트·최근 교정 …) + 그 탭 대화 최근 12개. 대화는 탭·맥락별로 DB 에.

## 3. 탭별

### ⓪ 오늘 `GET …/today`
- 전역 D-day (`discharge`) · 다음 지원 창 D-day (설정 '지원 창' 목록에서 가장 가까운 것 — 씨앗: 해외 2027 여름 지원 '지금~2027-01' · 국내 여름 인턴 '2027-04~05' · 겨울 '2027-10~11', '추정' 표시, ⑨에서 고침).
- 오늘 할 일 4줄 (체크는 각 탭 기록에서 자동): 코딩 2문제+ (`attempts`) · 발음 5~10분 (`pron_takes` 합) · AI 통화 20~30분 (통화 뒤 정리를 붙였나) · 스피치 카드 1 (`speech_takes`). 누르면 그 탭으로.
- 마감 7일 안 (최대 5 · 관심·지원 먼저) · 어제 바뀐 것 (`job_events` 지난 24시간: 새 공고 n · 닫힘 n · 지켜보는 회사 바뀜 — 각 3줄까지 + '더 보기'). AI 안 씀.

### ① 내 소개 `GET/POST …/profile`
- 칸: 학교·학과(건설환경→전기전자 전과)·학번·군(공군, 전역 2027-07-13, 말년 휴가 2027-06-01쯤)·복학 3-2(2027 가을)→4-1→4-2·졸업 2029-02·79학점·3.83/4.3·좋아한 과목·관심 분야·바라는 것(전환형 인턴·높은 보수)·영어·경험·자격증 (주인이 고침).
  첫 값은 `profile.SEED` 로 넣어 둠 (주인이 준 내용 그대로). 고치면 `profile_ver` +1 → 열린 공고 적합도를 하루 50개씩 천천히 다시.
- '한 줄 소개·1분 자기소개(한/영)' 만들기 단추 (Gemini good · 캐시) · '내 스펙 요약'(적합도·비교에 들어가는 400자)을 보여 줌.

### ② 학사·졸업
- 화면: ① 올리기(성적표·졸업 자가진단 화면 여러 장) → ② 읽은 표를 주인이 고치고 '맞아요' → ③ 남은 학점 막대(교양·전필·전선·총) → ④ 3학기 수강 계획 → ⑤ 재수강 평점 계산기.
- API: `POST …/upload (acad)` · `POST …/acad/parse {doc_ids}` · `GET/POST …/acad/courses` · `POST …/acad/req` · `POST …/acad/plan` · `GET …/acad`.
- 읽기: Gemini good(그림 입력) → `{courses:[{name,credits,grade,area(교양|전필|전선|기타),term,retake}], totals:{…}, remaining:{…}}`. **확인 전에는 저장하지 않음** (잘못 읽을 수 있음). 그림은 jobprep-data/acad 에만.
- 졸업 기준: 자가진단 화면의 '남은 학점'을 먼저 믿음. 요람은 `tools` 가 아니라 `academic.find_yoram()` 이 연세대 공개 페이지에서 PDF 링크를 찾으면 받아 둠(포털 로그인 절대 없음 · 못 찾으면 건너뜀). 기준 숫자는 주인이 '졸업 기준' 폼에서 확정 (전과생 기준이 달라서).
- 수강 계획: 2027-2·2028-1·2028-2 세 칸. 규칙(코드): 학기 최대 학점·전필 먼저·4-2 가볍게·겨울(12~2월) 계절학기 안 넣음(겨울 인턴과 겹침)·여름 계절은 선택. 과목 제안은 Gemini good 1번(캐시), 학점 합 검사는 코드.
- 재수강 계산기: 화면에서 바로 계산(JS) — 과목 고르고 기대 성적 넣으면 새 평점(4.3 · 4.5 환산). 재수강 성적 상한은 설정값(`retake_max_grade`, 기본 A0 — **주인 확인 필요**).
- 표: `acad_docs` · `acad_courses` · `acad_req` · `acad_plan`.

### ③ 공고
- 화면(위→아래): 거르기 줄(분야 칩·지역·시기 — 주인은 '관심 없음'으로 빼기만) → 카드 목록(시기별 묶음: **2027 여름**[`6월 시작`·`7/14 뒤 시작` 딱지, 한 묶음] · 2027 겨울 · 학기 중 원격 · 2028 여름 · 미정) → 지원 보드(관심→지원→서류 합격→면접→결과) → 닫힘(접힘) → 지켜보는 회사 → 제외 목록(되돌리기).
- 카드: 회사·직무·지역·시작·마감·전환?·학사 가능?·적합도 ★1~5(낮아도 다 보임 · 한 줄 까닭)·합격 사례 n건·단추 `관심 / 지원함 / 관심 없음`. '관심 없음' → `이 공고만 / 이 회사 / 이 분야` 고르기 → `exclusions`.
- API: `GET …/jobs?cat=&region=&season=` · `POST …/jobs/mark {id, status}` · `POST …/jobs/exclude {scope, key}` · `POST …/jobs/restore {id}` · `GET …/jobs/board` · `GET/POST …/jobs/watch` · `POST …/jobs/add {url}` (주인이 직접 넣기).
- 표: `companies(id,name,norm,cat,region,ats,token,career_url,growth_note,on)` · `jobs(id,src,src_id,company_id,title,loc,region,url,alt_urls,desc_hash,start,deadline,season,conversion,bachelor_ok,grad_ok,fit,fit_why,state(open|closed),closed_why,closed_at,miss,first_seen,last_seen,user_status,applied_at)` · `job_events(at,job_id,kind,note)` · `watch_pages(company_id,url,hash,changed_at,note)` · `exclusions(id,scope,key,label,at)`.
- 자세한 수집은 4장.

### ④ 합격 사례
- 화면: 분야→회사→직무 나무(접기) · 사례 카드(학교·전공·학점·영어·경험·자격증·면접 질문·준비 기간·출처 링크) · 이력서 본보기(링크+구조 요약) · '내 스펙과 비교' → 부족한 것 3가지.
- 모으기(월 05:50, ≤30쪽/주): ① 네이버 검색 API(서버에 있는 키 — 로그인 아님)·회사/학교 공식 글에서 "<회사> 인턴 합격 후기" 찾기 ② 공개 글만 읽기(로그인 벽·'이웃 공개'면 버림) ③ Gemini fast 로 칸 뽑기.
  이름·연락처는 지움, **글 전체는 저장하지 않고 요약 칸 + 링크만**. 이력서 본보기는 본인이 스스로 올린 것(GitHub·개인 사이트)과 학교·회사 공식 본보기만 (목록은 `cases.RESUME_SOURCES` 에 박고 링크만).
- 회사에 사례가 없으면 같은 분야(`companies.cat`)·같은 직무 낱말의 사례를 '비슷한 회사' 딱지로.
- API: `GET …/cases?cat=&company=&role=` · `POST …/cases/compare {company, role}` (Gemini good · 프로필 판·사례 묶음 해시로 캐시) · `POST …/cases/add {url}`.
- 표: `cases(id,cat,company,role,school,major,gpa,english,exp,certs,questions,prep,url,src_kind,at)` · `resumes(id,label,kind(official|self),url,summary)`.

### ⑤ 코딩 (CtCI) — 자세한 건 5장
- 화면: 오늘 몫(새 문제 2+ · 복습) → 문제 한 장 = 사다리 `① 개념 → ② 몸풀기 2~3 → ③ 책 문제 → ④ 힌트 3단 → ⑤ 풀이(잠금 단추) → ⑥ 한 단계 더`. 코드 칸(글상자, 등폭 글씨) + '채점'. 결과는 통과/실패 수와 실패한 입력. 장별 진도 막대·LeetCode 비슷한 문제 링크·'복무 중 모드' 스위치(개념 + 종이 풀이: 코드 칸 대신 '풀었음/막힘' 자기 채점 + 풀이 사진 메모).
- API: `GET …/code/today` · `GET …/code/q/<qid>` · `POST …/code/step {qid, step}` (힌트·풀이 열기 기록) · `POST …/code/run {qid, item, code}` · `POST …/code/feedback {attempt_id}` · `POST …/code/compare {qid}` · `POST …/code/self {qid, result}` · `GET …/code/progress`.

### ⑥ 학습 자료함
- 주인이 링크·메모·파일 이름을 넣고 꼬리표(분야·탭)로 거르는 단순한 목록 + 다른 탭에서 '자료함에 넣기'(교정 카드·개념 설명·합격 사례·대화 답). AI 없음(요약 단추만 Gemini fast). 
- API: `GET …/lib?tag=` · `POST …/lib/add` · `POST …/lib/del`. 표: `library(id,kind,title,url,body,tags,src_tab,at)`.

### ⑦ 스피치·인문학
- 스피치 카드(주 3장): 지미 카 공식 채널의 공개 영상 **자막만** (영상 내려받기 없음 — `yt-dlp --skip-download --write-auto-subs --sub-format json3`, 낱말별 시각). Gemini good 이 자막에서 농담 3개를 골라
  `{setup, twist, video_id, t_start}` 를 주고, **쉼(초)은 코드가 낱말 시각의 빈틈으로 잰다**(twist 첫 낱말 앞 빈틈). 카드에는 짧은 인용 몇 줄 + `youtu.be/<id>?t=` 링크만.
  유튜브가 서버를 막으면 그 주는 쉬고 한 줄 ('영상 주소 붙여 넣기' 칸으로 주인이 고른 영상 자막을 시도 · 설정 `use_proxy` 가 켜져 있으면 공개 읽기용 프록시 — 로그인 없는 공개 읽기라 프록시 원칙 안).
- 따라 말하기: 브라우저 MediaRecorder 로 녹음 → 올리기 → `audio.measure()` → 말 빠르기(낱말/초)·twist 앞 쉼·쉼 목록을 원본과 나란히 (막대 2개). AI 없음.
- 인문학: 하루 1주제(주 1번 7개 묶음 생성 · 3분 읽기 ≈ 900~1100자) + '30초로 설명' 녹음. 브라우저 음성 인식(Web Speech)이 되면 받아쓴 글을 Gemini fast 가 한 번 봐 줌(빠진 핵심·군말), 안 되면 녹음+자기 점검 3칸.
- API: `GET …/speech` · `POST …/upload (speech|human)` · `POST …/speech/take` · `GET …/human/today` · `POST …/human/say`. 표: `speech_cards` · `speech_takes` · `human_topics` · `human_takes`.

### ⑧ 영어
- 통화는 주인 폰의 Gemini 앱 Live 에서. 대시보드는: **오늘의 상대·상황 프롬프트 + 복사 단추 하나**.
  주 1번 Gemini good 한 번으로 7일치 `{name, personality, accent(US|UK|AU|SG|IN), situation, opener}` (상황 풀: 일상·여행·학교·일·면접·토론·이야기·유머 고루 — 면접은 주 1~2번까지).
  하루 프롬프트는 **코드가 조립**(AI 없음): 고정 규칙 글 + 그날 상대·상황 + 어제 자주 틀린 것 3개 + 오늘 리얼클래스 표현.
  고정 규칙: 진짜 사람처럼(짧게·되묻기·자기 얘기도) · 내 실수는 답 속에서 자연스럽게 고쳐 말해 주기(recast) · "just a moment / wait a second / let me think" 엔 **"Sure, take your time." 만 말하고 조용히 기다림** · "feedback" 이라고 하면 교정 3가지 · 20~30분.
- 통화 뒤: 대화 글 붙여 넣기(≤3만 자) → Gemini good 1번 → 교정 카드(내 문장 → 원어민 문장 → 까닭)·새 표현·자주 틀리는 것·리얼클래스 표현 썼나 체크. 결과가 다음 날 프롬프트에 들어감.
- 리얼클래스: 주인이 오늘 표현을 직접 적는 칸 (로그인·긁기 없음).
- 발음: 문장 소리 내어 읽기(5~10문장/일) → 6장 채점 → 소리별 점수(r/l·th·f/p·v/b·z·모음·강세)·약한 소리 연습 문장·점수 기록 그래프(SVG 선 하나, 날짜별 평균 — 색은 app.css 변수).
- API: `GET …/eng/today` · `POST …/eng/expr` · `POST …/eng/after {text}` · `GET …/eng/cards` · `GET …/pron/set` · `POST …/upload (pron)` · `GET …/pron/take/<id>` (채점 기다림 → 결과) · `GET …/pron/history`.
- 표: `eng_week` · `eng_sessions` · `eng_fix` · `eng_expr` · `pron_sents` · `pron_takes(id,sent_id,at,score,per_phone,state)`.

### ⑨ 설정
- 상한·쓴 양(Claude 오늘/이번 달 · Gemini 오늘 횟수·추정액) · 지원 창 날짜 · 분야/지역/시기 켜기 · 지켜보는 회사 · 제외 목록 · 졸업 기준 · 복무 중 모드 · 수집 '지금 한 번'(하루 2번까지) · 마지막 수집 결과(출처별 성공/실패). 상한 숫자 자체는 설정 파일 값(화면에선 보기만).

## 4. 공고 모으기

### 출처 (모두 로그인 없는 공개 주소 · `companies.py` 씨앗 표 — 토큰이 404 면 그 줄은 '지켜보는 회사'로 내려감)
| 종류 | 주소 꼴 | 닫힘 판정 재료 |
|---|---|---|
| Greenhouse | `https://boards-api.greenhouse.io/v1/boards/{token}/jobs?content=true` | 목록에 id 없음 |
| Lever | `https://api.lever.co/v0/postings/{token}?mode=json` | 목록에 id 없음 |
| Ashby | `https://api.ashbyhq.com/posting-api/job-board/{token}` (큼 — 20MB 상한·한 번에 하나) | 목록에 id 없음 |
| Workable | `https://apply.workable.com/api/v1/widget/accounts/{token}?details=true` | 〃 |
| SmartRecruiters | `https://api.smartrecruiters.com/v1/companies/{token}/postings` | 〃 |
| 회사 채용 페이지(HTML) | 회사별 주소 + 고르개(목록 링크 패턴) | 404/410 · 마감 문구 · 마감일 지남 |
| 국내 목록 | 링커리어 인턴 목록 · 캐치 · 사람인 공개 목록 (robots.txt 가 허용하는 길만 · 막히면 쉼) · 자소설닷컴은 서버 403 → 끔 | 〃 |
| 성장 판별 | DefiLlama `https://api.llama.fi/overview/fees?excludeTotalDataChart=true&excludeTotalDataChartBreakdown=true&dataType=dailyRevenue` · 거래소 거래량 CoinGecko `/api/v3/exchanges` · YC `https://yc-oss.github.io/api/companies/all.json` | — |

분야별 씨앗 (확인된 토큰은 굵게 · 나머지는 만들 때 한 번씩 확인하고 표에 `ats/token` 또는 `career_url` 로):
- 크립토 대형: **Binance(lever binance)** · **OKX(gh okx)** · **Coinbase(gh coinbase)** · **Kraken(ashby kraken.com)** · Bybit · Crypto.com · Bitget · Gate · KuCoin
- 크립토 성장(진짜 수익만): 주 1번 DefiLlama 30일 수익 상위 40 + 거래량 상위 CEX → 회사 표에 `growth_note`(수익 숫자). Hyperliquid·Lighter 는 공개 보드 없음 → 지켜보는 회사. 그 밖 예: Ethena·Pump·Jupiter·Phantom·Tether·Circle·Uniswap Labs·Aave Labs
- 국내 거래소: 두나무·빗썸·코빗·코인원 (채용 페이지 HTML)
- 마켓메이커·퀀트(크립토): **Wintermute(lever wintermute-trading)** · **Jump(gh jumptrading)** · **Flow Traders(gh flowtraders)** · GSR · Amber · Keyrock · Cumberland(DRW) · B2C2
- AI·테크 성장: **Anthropic(gh)** · **OpenAI(ashby)** · **Perplexity(ashby)** · **Cursor(ashby)** · **Databricks(gh)** · **Stripe(gh)** · Scale · Mistral · ElevenLabs / 국내: 토스·**당근(gh daangn)**·리벨리온·퓨리오사AI·업스테이지·뤼튼·트웰브랩스·몰로코·센드버드 / YC 목록에서 `isHiring`·최근 배치·한국/원격
- 빅테크: Google·Meta·Apple·Microsoft·Amazon·NVIDIA·네이버·카카오·라인·쿠팡 (채용 페이지 공개 JSON/HTML — 인턴 검색 주소 하나씩)
- 퀀트·트레이딩: Jane Street·Citadel/Citadel Securities·HRT·Optiver·IMC·Two Sigma·DRW·Susquehanna·Tower
- IB·금융·VC/PE: GS·MS·JPM·BofA·Citi·UBS(서울·홍콩·싱가포르 인턴) · 국내 증권사·운용사 인턴(링커리어·캐치 낱말) · VC 리서치 인턴
- 컨설팅: McKinsey·BCG·Bain 서울 RA/BA 인턴 · Kearney·Oliver Wyman·EY-Parthenon·Strategy& (공식 페이지 + 링커리어 낱말 'RA 인턴')
- 반도체(회로 설계 아님): 삼성전자·SK하이닉스·ASML·AMAT·Lam·TEL·KLA — 공정·장비·데이터·SW·기획 인턴

### 읽는 예절
호스트마다 3초 간격·동시 1개 · User-Agent 는 `settings.http.user_agent` 그대로 · 쿠키 없음 · ETag/If-Modified-Since · 응답 캐시 12시간 · 실패 3번이면 그 출처 24시간 쉼(화면 ⑨에 표시) ·
큰 JSON 은 파일로 받아 스레드에서 파싱 · robots.txt 가 막은 길은 안 감 · LinkedIn·블라인드·로그인 필요한 곳은 코드에 주소 자체를 넣지 않음. 프록시는 기본 끔 (`use_proxy` — 로그인 없는 공개 읽기에만).

### 거르기 2단
1. 규칙(코드·AI 없음): 제목/본문에 `intern|internship|인턴|체험형|채용연계|전환형|co-op|off-cycle|summer analyst|winter analyst|RA|research assistant` 있고, `PhD only|박사|senior|staff|lead|경력 n년` 없고, 지역이 한국·싱가포르·홍콩·미국·원격. 반도체 분야는 `회로설계|analog design|RTL|layout` 이면 뺌. 떨어진 것은 저장하지 않음(수만 셈).
2. Gemini fast (10개 묶음 · `desc_hash` 로 캐시 — 본문이 안 바뀌면 다시 안 부름): 아래 틀.

```
[system] 너는 채용 공고를 읽어 정해진 JSON 칸만 채운다. 공고에 없는 것은 null. 추측 금지.
[user] 지원자: {프로필 요약 400자 — 연세대 전기전자(전과)·3.83/4.3·2029-02 졸업·전역 2027-07-13·관심: IB/크립토/AI 성장/컨설팅·전환형 인턴·회로 설계 비선호 …}
공고 10개: [{id, company, title, location, text(앞 2,500자)}]
각 공고마다: start(YYYY-MM|null) · deadline(YYYY-MM-DD|"상시"|null) · conversion(true|false|null) ·
bachelor_ok(학사 재학생 지원 가능?) · grad_ok(2029-02 졸업 예정자 가능? — 졸업 연도 조건이 있으면 그걸로) ·
season(2027_summer_june|2027_summer_july|2027_winter|term_remote|2028_summer|other) · is_intern ·
fit(1~5: 5=전공·관심·시기 모두 맞음, 3=일부, 1=거의 안 맞음) · fit_why(한국어 40자 안)
```
`is_intern=false` 또는 `bachelor_ok=false` 또는 `grad_ok=false` 면 목록에서 빼되 '자동으로 뺀 것 n개' 접힌 줄에 남김(잘못 뺐을 때 되돌리기). ★ 는 낮아도 숨기지 않음.

### 겹침 없애기
1차 키 `src:token:src_id`. 다른 출처끼리는 `회사 norm + 제목 norm(괄호·연도·성별 표기 뺌) + 지역` 이 같거나, 회사가 같고 제목 낱말 겹침 ≥0.9 + 마감 같음 → 하나로(ATS 것이 주인, 나머지는 `alt_urls`). 주인이 누른 상태는 합쳐진 줄에 그대로.

### 닫힘 판정 (06:00·18:00 — 보드 단위로 한 번씩만 읽음)
- ATS: 보드를 **성공적으로** 읽었는데 그 id 가 없으면 `miss+1`, 2번 연속이면 닫힘(`closed_why="목록에서 사라짐"`). 보드 읽기 실패는 miss 로 안 셈.
- HTML: 404/410 → 바로 닫힘 · 목록으로 넘겨짐·`마감|접수마감|채용 종료|no longer accepting|position (has been )?filled|closed` → miss 2번 · `deadline < 오늘` → 바로 닫힘.
- 닫히면 `job_events('closed')` · 닫힘 칸(접힘)으로. `user_status` 가 지원함 이상이면 보드에 그대로 둠. 다시 나타나면 열림으로 되돌리고 `reopened` 기록.
- 지켜보는 회사: 채용 페이지 본문 글(태그·숫자 날짜 뺀 것) 해시가 바뀌면 `watch_pages.changed_at` + 바뀐 줄 앞 200자 → ⓪ '어제 바뀐 것'.

## 5. CtCI 뽑기 · 코드 돌리기 · 캐시

### 한 번 뽑기 `tools/jobprep_ctci_extract.py` (Claude·Gemini 안 씀 · `nice -n 19` · pypdf)
1. 1권 outline 에서 장 1~17 문제 구간·풀이 구간 시작 쪽을 얻음. 2권은 outline 이 비어 있을 수 있으니 쪽 머리글 `Solutions to Chapter N` 으로 구간을 잡음.
2. 문제 구간에서 `Interview Questions` 뒤 줄을 훑어 `^(\d{1,2})\s*[.,]\s*(\d{1,2})\s+제목:` 을 찾음. 번호가 제목과 떨어져 찍힌 쪽(1.5·1.6 처럼)은 "대문자로 시작하는 짧은 제목 + `:`" 줄을 차례대로 번호에 맞춤. 문제 글 = 다음 문제 머리 전까지 (`Hints:` 줄은 따로 떼어 냄).
3. 풀이 구간에서 같은 머리 + `SOLUTION` 을 찾아 풀이 시작 쪽(파일·쪽)과 끝(다음 풀이 머리 전)을 얻음. 제목으로 문제와 짝지음(번호가 깨져도 제목이 같으면 됨 · 글자 비슷함 ≥0.85).
4. 검증: 장별 개수가 `9,8,6,12,8,10,12,14,8,11,6,11,8,7,7,26,26`(합 189)과 같아야 함. 안 맞는 장·짝 없는 문제를 표로 찍고 종료 코드 1. 손으로 고칠 건 `ctci/overrides.json` (`{"1.5": {"title":…, "q_page":…, "sol_page":…}}`) — 다시 돌리면 덮음.
5. 결과: `ctci/index.json` `[{qid, ch, ch_title, title, q_part, q_page, sol_part, sol_page, sol_end, kind}]` + `ctci/text/<qid>.json {question, book_hints_raw, solution}`.
   `kind`: 1·2·3·4·5·8·10·16·17장 = `code`(120문제 · 샌드박스 채점) / 6·7·9·11·12·13·14·15장 = `explain`(69문제 · 글로 답하고 Gemini 가 책 풀이 요점과 견줌 — 시험 없음).
책 글은 git·로그·static·서비스 워커 캐시에 절대 안 들어감. API 답은 `no-store`.

### 사다리 만들기 — 문제마다 **딱 한 번** (`llm_cache` kind `ctci.ladder:<qid>:v1` · Gemini good · 날마다 다시 만들지 않음)
입력: 문제 글(짧음). 출력 JSON: `concept`(아무것도 모른다고 보고 아주 자세히 · 비유·글자 그림(ASCII) · 작은 예를 손으로 따라가기) · `warmups[2~3]`(중급부터 · 각 `{title, statement, fn, tests, ref_solution}`) ·
`hints[3]`(방향 → 자료구조 → 거의 풀이) · `twist`(한 단계 더) · `book.fn`(함수 이름·인자) · `book.tests[12~20]`(작은 것·경계·큰 것) · `book.ref_solution`(파이썬) · `leetcode[2~3]`(비슷한 문제 이름·주소 — 장 단위).
- **시험은 코드가 아니라 자료**: `{fn, cases:[{args, expected}], compare: exact|unordered|float|any_of, codec: none|linked_list|tree}`. 연결 리스트·트리는 runner 의 정해진 변환기(리스트 ↔ ListNode, 레벨 순서 ↔ TreeNode).
- **정답 맞추기**: `ref_solution` 을 같은 샌드박스에서 돌려 나온 값과 Gemini 가 적은 `expected` 가 **같은 경우만** 시험으로 씀(다르면 그 경우 버림 · 8개 아래로 남으면 한 번 더 만들고, 그래도 안 되면 '시험 준비 안 됨 — 자기 채점'으로). 맞고 틀림은 시험이 정하고 AI 가 정하지 않음.
- 03:30 에 다음 3문제를 미리. 주인이 그 전에 열면 그때 만들고 "만드는 중"(뒤에서 · 화면은 5초마다 확인).
- 몸풀기 조절: 그 장에서 최근 3문제 몸풀기를 힌트 없이 다 풀었으면 다음 문제는 몸풀기 1개만 보여 줌(나머지는 '더 보기'), 막혔으면 3개. 보여 주는 수만 바뀌고 다시 만들지 않음.

### 샌드박스 `sandbox.py`
- 넣는 것: `{runner 글, 사용자 코드, 시험 자료}` 를 **표준 입력 JSON** 으로. 나오는 것: 표준 출력 JSON `{cases:[{ok, got(앞 200자), ms, err}], stdout(앞 2KB)}`.
- 방식 A (기본 · 시작할 때 `sudo -n systemd-run --version` 이 되면): 
  `sudo -n systemd-run --quiet --wait --pipe --collect -p DynamicUser=yes -p PrivateNetwork=yes -p ProtectHome=yes -p ProtectSystem=strict -p PrivateTmp=yes -p NoNewPrivileges=yes -p MemoryMax=256M -p MemorySwapMax=0 -p CPUQuota=60% -p Nice=19 -p TasksMax=16 -p RuntimeMaxSec=12 /usr/bin/python3 -I -S -B -c "<stdin 에서 runner 를 읽어 exec 하는 한 줄>"`
  → 다른 사용자·네트워크 없음·/home 안 보임(.env 못 읽음)·메모리 256MB·12초. 인자는 모두 고정 글(사용자 글은 stdin 으로만 — 명령줄에 안 들어감).
- 방식 B (A 가 안 될 때 · 테스트 환경): `python3 -I -S -B` 하위 프로세스 · `env={}`(키가 안 넘어감) · 새 임시 폴더 · `preexec_fn` 에서 `setsid`·`nice 19`·`RLIMIT_AS 256MB`·`RLIMIT_CPU 5`·`RLIMIT_FSIZE 1MB`·`RLIMIT_NPROC`·`RLIMIT_NOFILE 32` · runner 가 `sys.addaudithook` 으로 `socket.*`·`subprocess`·`os.system`·`ctypes`·임시 폴더 밖 `open` 을 막음. (B 는 완전한 격리가 아님 — 화면 ⑨에 '격리: 약함' 표시)
- 공통: 경우마다 2초(runner 안 `signal.alarm`) · 전체 10초 넘으면 프로세스 묶음째 죽임 · 동시에 1개(asyncio.Lock) · 1분 6번·하루 300번 · 코드 50KB·출력 64KB 상한 · 여유 메모리 400MB 아래면 "잠깐 뒤 다시".
- 채점 뒤 대화창에 자동 한 마디(Gemini fast 1번 · `POST …/code/feedback`): 왜 틀렸나(실패 입력·기대값·내 값) · 시간/공간 복잡도 · 고칠 점 · 다음 힌트(풀이는 말하지 않음). 통과했으면 복잡도·더 나은 길만.
- '책 풀이와 비교'는 **누를 때만** 책 풀이 글을 Gemini 에 보냄(문제당 1번 캐시). 자바·C++ 는 나중(runner 만 늘리면 됨 — 지금은 파이썬).

### 복습·몫
- 간격 복습: 통과한 날부터 1·3·7·21일 뒤 `reviews(qid, due, n)`. 힌트 3단·풀이를 열고 풀었으면 간격을 처음(1일)부터. 복습은 책 문제만 다시(몸풀기 없음).
- 주간 몫: `남은 문제 ÷ (오늘~book_finish_by 주 수)` 올림, 하루 최소 2. `code` 문제를 장 순서(1→2→3→4→8→10→5→16→17)로, `explain` 장은 주 2개씩 섞음. 밀리면 ⓪에 '이번 주 n개 남음'.
- 표: `ctci_progress(qid, state, opened_hints, saw_solution, solved_at, warmups_shown)` · `attempts(id, qid, item, code, passed, total, fail_case, at)` · `reviews`.

## 6. 발음 채점 · 쉼 재기

### 모델: `onnx-community/wav2vec2-lv-60-espeak-cv-ft-ONNX` 의 `onnx/model_int8.onnx` (318MB · Apache-2.0 · 음소(IPA) CTC)
- 고른 까닭: 음소를 바로 내는 공개 모델 중 ONNX 양자화본이 이미 있어 torch 없이 `onnxruntime`(CPU)만으로 돎. 작은 모델(bookbot gruut, 95M)은 한 사람 목소리로만 배워 외국인 억양에 약하고 ONNX 가 없어 변환에 torch 가 필요.
- 자원(추정 — 만들 때 실제로 재서 이 글에 적기): 메모리 최고 0.7~0.9GB · 10초 소리에 2 CPU 로 5~10초(불러오기 3~5초 포함) · 디스크 0.33GB.
- **서버 지키기**: 딜봇 프로세스 안에서 안 돌림. `nice -n 19 python -m dealbot.dashboard.jobprep.pron_worker <wav> <phones.json>` 을 그때그때 띄우고 끝나면 내려감(상주 안 함) · `intra_op_num_threads=1` ·
  한 번에 1개(줄 세움) · 여유 메모리 < `pron_min_free_mb`(1000) 이면 띄우지 않고 '채점 기다림'으로 두었다가 tick 이 나중에 · 소리 15초 상한 · 60초 넘으면 죽임 · 방식 A 가 되면 `systemd-run -p MemoryMax=1200M` 으로 감쌈.
- 목표 음소: 문장 → CMUdict(ARPAbet·강세 숫자) → espeak IPA 로 바꾸는 고정 표(39개, 코드에). espeak 설치 안 함. 연습 문장은 **모든 낱말이 CMUdict 에 있는 것만**(씨앗 60문장을 코드에 · Gemini 가 만든 문장도 이 검사를 통과해야 저장).
- 점수: ① 로짓으로 목표 음소열을 CTC 강제 정렬(numpy Viterbi) → 음소별 구간 ② GOP = 구간 안 목표 음소 평균 로그확률 − 가장 높은 다른 음소 → 0~100 으로(곡선은 상수 2개, 테스트로 고정) ③ 자유 인식(greedy) 결과로 '이렇게 들렸어요'(r→l, θ→s 같은 바뀜).
  소리 묶음별 평균(r/l · th · f/p · v/b · z · 긴/짧은 모음 · æ/ɛ) + 강세(강세 모음 구간 길이·세기가 이웃보다 큰가 — '참고' 딱지). 약한 묶음 2개의 연습 문장을 다음 날 세트에 넣음.
- 흐름: 녹음(webm/opus 또는 mp4/aac) → 올리기 → ffmpeg 로 16kHz 모노 wav → 일꾼 → `pron_takes` → 화면은 2초마다 `GET …/pron/take/<id>`.
- `tools/jobprep_setup.py`: 모델·vocab·`cmudict.dict`(cmusphinx 공개 파일) 를 `models/` 에 받음(sha256 적어 둠 · 이어 받기 · nice 19). 없으면 발음 칸은 "준비 중"만 보이고 나머지는 그대로.

### 쉼 재기 `audio.measure()` — shorts-research 의 방식을 옮겨 적음 (다른 저장소를 import 하지 않음)
- 본보기: `/home/ubuntu/shorts-research/subchannel/sound/senior/meas.py` (ffmpeg `silencedetect=noise=-38dB:d=0.08` · 앞뒤 무음 빼기 · 안쪽 무음 중 0.25초 넘는 것 = 쉼 · 말하는 시간 = 길이 − 무음) 와 `director/aitell/pause_check.py`·`prosody.py`.
- 내 녹음: 위 방식 그대로 → `{dur, speech_s, pauses:[{at, len}], wps}`. 원본(지미 카): 자막 낱말 시각의 빈틈(≥0.25초)으로 같은 꼴을 만듦. 견줌 = 말 빠르기 비율 · twist 앞 쉼 길이 차이 · 쉼 수.

## 7. 테스트 · 올리기

### 만드는 쪽이 넣을 테스트 (네트워크·진짜 AI·진짜 모델 없이 — 가짜 http·가짜 llm 주입)
- `test_jobprep_shell.py`: 탭 링크·`APP_FILES`·`PAGE_PREFIXES`·로그인 없이 302/401·CSP(`media-src 'self' blob:`)·POST 는 guard 없으면 403·static 파일에 데이터 낱말 없음.
- `test_jobprep_store.py`: 표 만들기·data_dir 밖 경로 거절·파일 권한 600.
- `test_jobprep_llm.py`: 캐시 키·두 번째 호출은 안 부름·Claude 상한(하루·달·첫 주)에서 안 부르고 429·장부에 `jobprep.` 줄·**CLI 로 안 넘어감**·키 글이 로그/답에 없음.
- `test_jobprep_collectors.py`: 출처별 픽스처 JSON → 같은 꼴 줄·규칙 거르기(인턴/박사/회로 설계)·겹침 합치기·20MB 상한·실패 3번 쉼·금지 호스트(linkedin·blind) 주소가 코드에 없음.
- `test_jobprep_jobs.py`: miss 2번 닫힘·읽기 실패는 miss 아님·마감 지남 바로 닫힘·지원한 건 보드에 남음·다시 열림·제외/되돌리기·시기 묶음(6월·7/14 뒤가 한 묶음)·★ 낮아도 보임·지켜보는 회사 해시.
- `test_jobprep_ctci.py`: 가짜 쪽 글로 문제/풀이 짝짓기·장별 개수 검증 실패 시 종료 코드 1·overrides·간격 1/3/7/21·힌트 열면 처음부터·주간 몫 계산·몸풀기 수 조절.
- `test_jobprep_sandbox.py` (방식 B): 맞음/틀림/무한 루프 시간 초과/메모리 폭주/소켓·파일 밖 열기 막힘/환경변수 비어 있음/출력 상한/연결 리스트·트리 변환/ref 와 expected 다르면 버림.
- `test_jobprep_audio.py`: silencedetect 글 파싱·쉼 계산·자막 낱말 시각 → 쉼 (ffmpeg 는 가짜 stderr).
- `test_jobprep_pron.py`: ARPAbet→IPA 표 39개 다 있음·CMUdict 에 없는 낱말 문장 거절·가짜 로짓으로 정렬·GOP 점수 순서·메모리 부족이면 기다림(일꾼 안 띄움). `onnxruntime`·`numpy` 는 `pytest.importorskip`.
- `test_jobprep_tabs.py`: 오늘 4줄 체크·D-day·마감 5개 상한·영어 프롬프트 조립(규칙 문장 "Sure, take your time." 포함·어제 실수 들어감)·재수강 계산·학점 막대·대화 맥락 6천 토큰 상한.
- 기존 테스트 중 탭 수·CSS 를 세는 것(`test_dash_shell.py` 등)이 깨지면 같이 고침.

### 돌리는 법 (저장소 CLAUDE.md)
`cd /home/ubuntu/wt-jobprep && PYTHONPATH=src nice -n 19 /home/ubuntu/coupang-deal-bot/.venv/bin/python -m pytest -q -p no:cacheprovider` — 백그라운드로.
원래 실패 3개(test_deploy_lightsail selftest 템플릿 · test_toss_more 2개) 말고 실패가 있으면 push 안 함. 먼저 `tests/test_jobprep_*.py tests/test_dash_*.py` 만 돌리고, push 전에 전체 한 번.
**worktree 에 `.venv` 를 만들거나 링크하지 않는다 · `git add -A` 금지(파일 이름을 적어 add) · 돌아가는 venv 에 손으로 pip install 하지 않는다**(새 의존성이 필요한 테스트는 importorskip).

### 올리기
1. `jobprep` 가지에서 커밋(파일 이름 적어 add · `git status` 에 `.venv`·`.env`·jobprep-data 없음 확인) → main 에 fast-forward → `git push origin main` → 서버가 자동 업데이트(`deploy/lightsail/auto-update.sh` 가 pyproject 바뀜을 보고 `pip install -e .`).
2. 올린 뒤 서버에서 한 번(모두 `nice -n 19` · 백그라운드): `python tools/jobprep_ctci_extract.py` → `python tools/jobprep_setup.py`.
3. 확인: `/dashboard/jobprep/` 열림 · ⑨에 격리 방식·모델 준비·출처별 첫 수집 결과 · 다음 06:00 수집 기록. `docs/devlog.md` 한 줄 · `docs/DASHBOARD.md` '취준' 절 · 보드 '최근 소식'(메인이 씀).
4. 되돌리기: 설정 `dashboard.jobprep.enabled: false` (경로·tick 모두 꺼짐 · 탭 링크 숨김) — 코드를 되돌리지 않고 끌 수 있게.

## 8. 만드는 순서 (Opus 한 번에 · 확인 지점 ✔ 마다 그 묶음 테스트 통과)

1. **틀**: config · store · page · 함께 쓰는 파일 줄 · `jobprep.js` 탭 줄·빈 화면 10개 · `jobprep.css`. ✔ shell·store 테스트, 기존 `test_dash_*` 그대로 통과.
2. **AI·대화**: `llm.py`(캐시·상한·장부) · `chat.py` · 대화창 UI · ⑨ 쓴 양 표시. ✔ llm 테스트(상한에서 안 부름).
3. **① 내 소개 · ⑥ 자료함** (단순 CRUD — 틀 검증). ✔
4. **③ 공고**: companies 씨앗 → collectors(ATS 5종 → HTML → 국내 목록 → 성장 목록) → 규칙 거르기 → Gemini 뽑기 → 겹침 → 닫힘 → 화면·보드·제외·지켜보기 → tick. ✔ collectors·jobs 테스트. (씨앗 토큰은 만들 때 한 번씩만 확인 — 회사당 요청 1번)
5. **④ 합격 사례**. ✔
6. **⑤ 코딩**: extract 도구(진짜 PDF 로 돌려 189개 확인 — 안 맞으면 overrides) → sandbox(B 먼저, A 탐지) → ladder 생성·검증 → 화면 → 복습·몫 → 피드백. ✔ ctci·sandbox 테스트 + 진짜 문제 1개(1.1) 끝까지.
7. **audio → ⑦ 스피치·인문학 → ⑧ 영어(프롬프트·통화 뒤·리얼클래스) → 발음(setup 도구 · 일꾼 · 화면)**. ✔ audio·pron 테스트 + 실제 메모리·시간 재서 6장에 적기.
8. **② 학사** (주인 화면이 아직 없음 — 가짜 그림 픽스처로 흐름만). ✔
9. **⓪ 오늘** (다른 탭 기록을 모음 — 마지막). ✔ tabs 테스트.
10. 전체 테스트(백그라운드) → DASHBOARD.md·devlog → Fable 검토 → 올리기.
오래 걸리는 것(전체 테스트·모델 받기·PDF 뽑기)은 백그라운드로 돌리고 그 사이 다음 조각을 만든다.

## 9. 남은 위험 · 주인께 여쭐 것 (짧게)

1. **Gemini 가 진짜 0원인지 모름**: 서버 공용 Gemini 키는 장부에 달 $20 예산이 잡혀 있어 유료 키일 수 있음. 취준 몫 달 $3 추정 상한을 걸어 두었지만 0원을 지키려면 무료 키(`JOBPREP_GEMINI_API_KEY`)가 따로 필요 — 주인 결정. 무료 키는 입력이 구글 학습에 쓰일 수 있음(책 문제 글·성적표 그림이 들어감).
2. **Claude $40 가 어디 몫인지**: 11/22 나눔(블로그 $120·쇼츠 $40·퍼프덱스 $20·남김 $20)에 취준이 없음. 지금 설계로는 취준 호출이 블로그 서버 장부($120)에 같이 쌓임 — 메인 6·주인과 맞춰야 함(남김 $20 + ? 또는 블로그 몫에서).
3. **샌드박스**: 방식 A 는 딜봇이 `sudo systemd-run` 을 부름(서버에 sudo 가 비밀번호 없이 됨을 전제). 안 되면 방식 B(약한 격리)로 Gemini 가 만든 참고 풀이도 돌게 됨 — B 일 때는 참고 풀이 실행을 끄고 '자기 채점'으로 둘지 검토 때 정함.
4. **메모리**: 발음 모델 최고 0.9GB — 블로그 글쓰기·크롬이 도는 시간엔 '채점 기다림'이 길어질 수 있음. 실측이 1.2GB 를 넘으면 `model_q4f16`(197MB) 로 바꾸거나 채점을 밤에 몰아서.
5. **막힐 수 있는 출처**: 유튜브 자막(서버 IP 봇 확인)·자소설닷컴(403)·국내 목록 사이트 구조 바뀜·빅테크/IB 채용 페이지(자바스크립트로만 그림). 막히면 그 출처만 쉬고 ⑨에 표시 — 로그인·우회는 하지 않음.
6. 그 밖: CtCI 글자 깨짐으로 189개가 한 번에 안 맞을 수 있음(overrides 로 손봄) · 전과생 졸업 기준·재수강 상한은 주인 확인값 · 아래 탭이 8칸이 되어 좁아짐(48px) · 합격 사례는 공개 글이 적은 회사(해외 크립토)엔 거의 없을 수 있음.
