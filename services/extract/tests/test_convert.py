import hashlib

from careonex_extract.convert import html_to_markdown, pdf_to_markdown

PAGE = """<!doctype html><html><head><title>Jersey Assistance for Community Caregiving (JACC)</title>
<script>window.nonce="{nonce}";</script></head>
<body><nav><ul><li><a href="/">Home</a></li><li><a href="/services">Services</a></li></ul></nav>
<main><article>
<h1>Jersey Assistance for Community Caregiving (JACC)</h1>
<p>JACC is a state-funded program that provides in-home services to individuals age 60 and older who meet
nursing home level of care and wish to remain at home. It serves people who are not enrolled in Medicaid.</p>
<h2>Financial eligibility</h2>
<table><tr><th>Household</th><th>Monthly income</th><th>Resources</th></tr>
<tr><td>Individual</td><td>$4,855</td><td>$40,000</td></tr>
<tr><td>Couple</td><td>$6,582</td><td>$60,000</td></tr></table>
<p>Participants pay a cost share on a sliding scale. Services are capped at $1,090 per month.</p>
</article></main>
<footer>Last rendered {stamp} · © State of New Jersey</footer></body></html>"""


def test_html_boilerplate_and_nonces_do_not_change_text():
    a = html_to_markdown(PAGE.format(nonce="abc123", stamp="2026-10-03 21:14:02").encode())
    b = html_to_markdown(PAGE.format(nonce="zzz999", stamp="2026-10-04 06:01:55").encode())
    assert a == b
    assert hashlib.sha256(a.encode()).hexdigest() == hashlib.sha256(b.encode()).hexdigest()
    assert "nursing home level of care" in a
    assert "$4,855" in a
    assert "nonce" not in a and "Last rendered" not in a and "- Home" not in a
    # The intro paragraph before the first h2 is the eligibility text; it must survive.
    assert "age 60 and older" in a and "not enrolled in Medicaid" in a


def test_nj_gov_like_layout_keeps_intro_before_accordion():
    page = """<html><head><title>Division of Aging Services | JACC</title></head><body>
    <header><a href="/">Skip to Content</a><div class="menu"><a>Home</a><a>Services</a></div></header>
    <main id="main-content">
      <h1>Jersey Assistance for Community Caregiving (JACC)</h1>
      <p>JACC is a State-funded program for individuals age 60 and older who meet clinical eligibility for nursing home level of care.</p>
      <p>Individuals who require assistance with a minimum of three activities of daily living (ADL) such as bathing, toileting, dressing.</p>
      <div class="accordion"><h2>What services does JACC offer?</h2><ul><li>Chore services</li><li>Respite care</li></ul></div>
    </main>
    <footer>© State of New Jersey <span>rendered 12:00:01</span></footer></body></html>""".encode()
    md = html_to_markdown(page)
    assert md.startswith("# Jersey Assistance")
    assert "age 60 and older" in md and "three activities of daily living" in md
    assert "- Chore services" in md and "## What services does JACC offer?" in md
    assert "Skip to Content" not in md and "rendered 12:00" not in md


def test_pdf_headings_and_text_survive():
    import pymupdf

    doc = pymupdf.open()
    page = doc.new_page()
    page.insert_text((72, 72), "MLTSS Application Guidance", fontsize=20)
    page.insert_text((72, 120), "You must own $2,000 or less in total resources.", fontsize=11)
    page.insert_text((72, 140), "A caseworker will review the five-year look-back period.", fontsize=11)
    data = doc.tobytes()
    md = pdf_to_markdown(data)
    assert "MLTSS Application Guidance" in md
    assert "$2,000" in md
    assert "look-back" in md
    assert md.endswith("\n") and "\n\n\n" not in md


def test_nested_boilerplate_does_not_crash():
    page = """<html><head><title>T</title></head><body><main>
    <div class="menu"><div class="social"><span class="share-btn">Share</span></div><a id="skip-link">skip</a></div>
    <p>Real content about Statewide Respite Care Program eligibility stays. It is long enough to count as the main region of this page.</p>
    <div id="toolbar"><button class="search">s</button></div></main></body></html>""".encode()
    md = html_to_markdown(page)
    assert "Real content about Statewide Respite" in md
    assert "Share" not in md and "skip" not in md


def test_main_nested_inside_nav_like_nj_gov():
    page = """<html><head><title>Division of Aging Services | JACC</title></head><body>
    <div class="container-fluid nj-nav"><nav class="navbar navbar-expand-lg">
      <ul class="navbar-nav"><li>Home</li><li>Programs</li></ul>
      <main class="no-gutters">
        <section id="content"><div id="introContent">
          <h1>Jersey Assistance for Community Caregiving (JACC)</h1>
          <p>JACC is a State-funded program for individuals age 60 and older who meet clinical eligibility for nursing home level of care and who desire to remain in their homes.</p>
        </div>
        <h2>What services does JACC offer?</h2><ul><li>Chore services</li></ul></section>
      </main>
    </nav></div></body></html>""".encode()
    md = html_to_markdown(page)
    assert "age 60 and older" in md and "nursing home level of care" in md
    assert "## What services does JACC offer?" in md and "- Chore services" in md
    assert "Programs" not in md.split("\n")[0]


def test_va_gov_wrapper_with_sidebar_hint_keeps_article():
    page = """<html><head><title>Aid And Attendance | Veterans Affairs</title></head><body><main>
    <div id="content" class="interior"><div class="vads-grid-row va-sidebarnav-wrapper">
      <nav class="va-sidebarnav"><ul><li>Pension</li><li>Survivors pension</li></ul></nav>
      <article class="usa-content"><h1>VA Aid and Attendance benefits and Housebound allowance</h1>
      <p>VA Aid and Attendance or Housebound benefits provide monthly payments added to the amount of a monthly VA pension for qualified Veterans and survivors who need help with daily activities or are housebound.</p>
      <p>See the brochure: <a href="https://www.va.gov/files/Aid_(2026).pdf">Aid and Attendance (PDF)</a>.</p></article>
    </div></div></main></body></html>""".encode()
    md = html_to_markdown(page)
    assert "monthly payments added" in md
    assert "Survivors pension" not in md
    assert "Aid and Attendance (PDF)." in md and "va.gov/files" not in md
