"""Tests for form request generation and stateful form submission.

Covers browser successful controls semantics:
- disabled fieldsets (with first-legend exception)
- disabled options and disabled optgroups in select elements
- readonly controls, unnamed controls, and non-submit buttons
- submit button overrides (formaction, formmethod, formenctype)
- external form-associated controls (form attribute)
- caller-provided request data combined with form data
"""
import io
import sys

import pytest
import setpath  # noqa:F401, must come before import mechanicalsoup
from bs4 import BeautifulSoup
from utils import setup_mock_browser

from mechanicalsoup.browser import Browser
from mechanicalsoup.form import Form


# ---------------------------------------------------------------------------
# Helper: extract form data from get_request_kwargs without network
# ---------------------------------------------------------------------------

def _form_data(html, url="http://example.com", choose_submit_btn=None,
               submit_false=False, **kwargs):
    """Parse HTML, optionally choose a submit, return request kwargs."""
    soup = BeautifulSoup(html, "lxml")
    form_tag = soup.find("form")
    if choose_submit_btn is not None or submit_false:
        form_obj = Form(form_tag)
        if submit_false:
            form_obj.choose_submit(False)
        else:
            form_obj.choose_submit(choose_submit_btn)
        form_tag = form_obj.form
    return Browser.get_request_kwargs(form_tag, url, **kwargs)


def _post_data(html, **kwargs):
    """Return just the data list from a POST form."""
    result = _form_data(html, **kwargs)
    return result.get("data", [])


def _get_params(html, **kwargs):
    """Return just the params list from a GET form."""
    result = _form_data(html, **kwargs)
    return result.get("params", [])


# ===========================================================================
# 1. DISABLED FIELDSET
# ===========================================================================

class TestDisabledFieldset:
    """Controls in a disabled fieldset are omitted, except those in the
    fieldsets first legend element."""

    def test_disabled_fieldset_omits_controls(self):
        """Controls in a disabled fieldset should not be submitted."""
        html = """
        <form method="post" action="http://example.com/post">
          <fieldset disabled>
            <input name="blocked" value="yes"/>
            <textarea name="notes">stuff</textarea>
            <select name="choice">
              <option value="a" selected>A</option>
            </select>
          </fieldset>
          <input name="free" value="ok"/>
        </form>
        """
        data = _post_data(html, submit_false=True)
        names = [name for name, _ in data]
        assert "blocked" not in names
        assert "notes" not in names
        assert "choice" not in names
        assert ("free", "ok") in data

    def test_first_legend_controls_not_disabled(self):
        """Controls inside the FIRST legend of a disabled fieldset follow
        browser behavior: they ARE submitted."""
        html = """
        <form method="post" action="http://example.com/post">
          <fieldset disabled>
            <legend>
              <input name="legend_input" value="from_legend"/>
            </legend>
            <input name="outside_legend" value="blocked"/>
          </fieldset>
        </form>
        """
        data = _post_data(html, submit_false=True)
        assert ("legend_input", "from_legend") in data
        assert ("outside_legend", "blocked") not in data

    def test_second_legend_still_disabled(self):
        """Only the FIRST legend child is exempt; controls in a second legend
        are still disabled by the fieldset."""
        html = """
        <form method="post" action="http://example.com/post">
          <fieldset disabled>
            <legend><input name="first" value="ok"/></legend>
            <legend><input name="second" value="no"/></legend>
          </fieldset>
        </form>
        """
        data = _post_data(html, submit_false=True)
        assert ("first", "ok") in data
        assert ("second", "no") not in data

    def test_nested_fieldset_disabled(self):
        """A nested fieldset inside a disabled parent inherits disabling."""
        html = """
        <form method="post" action="http://example.com/post">
          <fieldset disabled>
            <fieldset>
              <input name="nested" value="nope"/>
            </fieldset>
          </fieldset>
        </form>
        """
        data = _post_data(html, submit_false=True)
        assert ("nested", "nope") not in data

    def test_non_disabled_fieldset_passes(self):
        """Controls in a fieldset WITHOUT disabled are submitted normally."""
        html = """
        <form method="post" action="http://example.com/post">
          <fieldset>
            <input name="inner" value="yes"/>
          </fieldset>
        </form>
        """
        data = _post_data(html, submit_false=True)
        assert ("inner", "yes") in data


# ===========================================================================
# 2. DISABLED OPTIONS / OPTGROUPS
# ===========================================================================

class TestDisabledOptions:
    """Disabled options and disabled optgroups must not leak into
    submitted select values."""

    def test_disabled_selected_option_not_submitted(self):
        """A disabled option that has selected attribute should be skipped."""
        html = """
        <form method="post" action="http://example.com/post">
          <select name="menu">
            <option value="a" disabled selected>A</option>
            <option value="b">B</option>
          </select>
        </form>
        """
        data = _post_data(html, submit_false=True)
        # The disabled selected option 'a' should NOT be submitted;
        # browser falls back to first non-disabled option 'b'.
        assert ("menu", "b") in data
        assert ("menu", "a") not in data

    def test_disabled_optgroup_options_not_submitted(self):
        """Options inside a disabled optgroup should not be submitted
        even if they carry the selected attribute."""
        html = """
        <form method="post" action="http://example.com/post">
          <select name="menu">
            <optgroup label="Group1" disabled>
              <option value="g1a" selected>G1-A</option>
              <option value="g1b">G1-B</option>
            </optgroup>
            <optgroup label="Group2">
              <option value="g2a">G2-A</option>
            </optgroup>
          </select>
        </form>
        """
        data = _post_data(html, submit_false=True)
        # g1a is selected but inside disabled optgroup -> skipped
        # fallback -> first non-disabled option = g2a
        assert ("menu", "g2a") in data
        assert ("menu", "g1a") not in data

    def test_single_select_fallback_skips_disabled(self):
        """When no option is explicitly selected in a single-select, the
        browser picks the first NON-disabled option."""
        html = """
        <form method="post" action="http://example.com/post">
          <select name="pick">
            <option value="x" disabled>X</option>
            <option value="y" disabled>Y</option>
            <option value="z">Z</option>
          </select>
        </form>
        """
        data = _post_data(html, submit_false=True)
        assert ("pick", "z") in data
        assert ("pick", "x") not in data
        assert ("pick", "y") not in data

    def test_all_options_disabled_submits_nothing(self):
        """If every option is disabled, browser submits nothing for
        that select."""
        html = """
        <form method="post" action="http://example.com/post">
          <select name="nope">
            <option value="a" disabled>A</option>
            <option value="b" disabled>B</option>
          </select>
          <input name="other" value="yes"/>
        </form>
        """
        data = _post_data(html, submit_false=True)
        names = [n for n, _ in data]
        assert "nope" not in names
        assert ("other", "yes") in data

    def test_multi_select_disabled_option(self):
        """In a multi-select, disabled selected options are excluded."""
        html = """
        <form method="post" action="http://example.com/post">
          <select name="multi" multiple>
            <option value="a" selected>A</option>
            <option value="b" selected disabled>B</option>
            <option value="c" selected>C</option>
          </select>
        </form>
        """
        data = _post_data(html, submit_false=True)
        assert ("multi", "a") in data
        assert ("multi", "c") in data
        assert ("multi", "b") not in data

    def test_multi_select_disabled_optgroup(self):
        """In a multi-select, options in disabled optgroup are excluded."""
        html = """
        <form method="post" action="http://example.com/post">
          <select name="multi" multiple>
            <option value="free" selected>Free</option>
            <optgroup disabled>
              <option value="locked" selected>Locked</option>
            </optgroup>
          </select>
        </form>
        """
        data = _post_data(html, submit_false=True)
        assert ("multi", "free") in data
        assert ("multi", "locked") not in data


# ===========================================================================
# 3. READONLY, UNNAMED CONTROLS, NON-SUBMIT BUTTONS
# ===========================================================================

class TestReadonlyAndSpecialControls:
    """Readonly controls are submitted; unnamed and non-submit buttons
    are not."""

    def test_readonly_input_is_submitted(self):
        """An input with readonly attribute is still submitted."""
        html = """
        <form method="post" action="http://example.com/post">
          <input name="locked" value="immutable" readonly/>
          <input name="normal" value="mutable"/>
        </form>
        """
        data = _post_data(html, submit_false=True)
        assert ("locked", "immutable") in data
        assert ("normal", "mutable") in data

    def test_readonly_textarea_is_submitted(self):
        """A textarea with readonly attribute is still submitted."""
        html = """
        <form method="post" action="http://example.com/post">
          <textarea name="ro" readonly>preserved</textarea>
        </form>
        """
        data = _post_data(html, submit_false=True)
        assert ("ro", "preserved") in data

    def test_unnamed_controls_are_omitted(self):
        """Controls without a name attribute are never submitted."""
        html = """
        <form method="post" action="http://example.com/post">
          <input value="no_name"/>
          <textarea>no name</textarea>
          <select><option value="x" selected>X</option></select>
          <input name="named" value="yes"/>
        </form>
        """
        data = _post_data(html, submit_false=True)
        assert len(data) == 1
        assert data[0] == ("named", "yes")

    def test_non_submit_buttons_omitted_from_data(self):
        """Buttons of type=button and type=reset never contribute
        to submitted form data."""
        html = """
        <form method="post" action="http://example.com/post">
          <button type="button" name="btn" value="click">Click</button>
          <button type="reset" name="rst" value="reset">Reset</button>
          <input name="field" value="data"/>
          <input type="submit" name="go" value="Go"/>
        </form>
        """
        data = _post_data(html, choose_submit_btn="go")
        names = [n for n, _ in data]
        assert "btn" not in names
        assert "rst" not in names
        assert ("field", "data") in data
        assert ("go", "Go") in data


# ===========================================================================
# 4. SUBMIT BUTTON OVERRIDES
# ===========================================================================

class TestSubmitOverrides:
    """Choosing a submit button preserves only that button and honors
    its formaction, formmethod, and formenctype without changing
    unrelated form defaults."""

    def test_formaction_on_chosen_input_submit(self):
        """formaction on input[type=submit] overrides the form action."""
        html = """
        <form method="post" action="http://example.com/default">
          <input name="x" value="1"/>
          <input type="submit" name="go" value="Go"
                 formaction="http://example.com/override"/>
        </form>
        """
        result = _form_data(html, choose_submit_btn="go")
        assert result["url"] == "http://example.com/override"

    def test_formaction_on_chosen_button_submit(self):
        """formaction on button[type=submit] overrides the form action."""
        html = """
        <form method="post" action="http://example.com/default">
          <input name="x" value="1"/>
          <button type="submit" name="go" value="Go"
                  formaction="http://example.com/btn_override">Go</button>
        </form>
        """
        result = _form_data(html, choose_submit_btn="go")
        assert result["url"] == "http://example.com/btn_override"

    def test_formaction_ignored_on_non_submit_button(self):
        """A non-submit button formaction must NOT override the action."""
        html = """
        <form method="post" action="http://example.com/correct">
          <button type="button" name="trap" value="x"
                  formaction="http://example.com/wrong">Trap</button>
          <input name="field" value="val"/>
          <input type="submit" name="go" value="Go"/>
        </form>
        """
        result = _form_data(html, choose_submit_btn="go")
        assert result["url"] == "http://example.com/correct"

    def test_formmethod_overrides_form_method(self):
        """Submit button formmethod overrides the form method."""
        html = """
        <form method="post" action="http://example.com/target">
          <input name="q" value="search"/>
          <input type="submit" name="go" value="Go" formmethod="get"/>
        </form>
        """
        result = _form_data(html, choose_submit_btn="go")
        assert result["method"] == "get"
        # Data should be in params not data for GET
        assert "params" in result
        assert ("q", "search") in result["params"]

    def test_formenctype_overrides_form_enctype(self):
        """Submit button formenctype can force multipart encoding."""
        html = """
        <form method="post" action="http://example.com/target">
          <input name="field" value="val"/>
          <input type="submit" name="go" value="Go"
                 formenctype="multipart/form-data"/>
        </form>
        """
        result = _form_data(html, choose_submit_btn="go")
        # When enctype is multipart, the files dict should be truthy
        # (MechanicalSoup uses a DictThatReturnsTrue trick)
        assert bool(result.get("files")) is True

    def test_override_does_not_change_form_defaults(self):
        """Submitting with overrides does not mutate the form element
        own action/method/enctype attributes."""
        html = """
        <form method="post" action="http://example.com/default"
              enctype="application/x-www-form-urlencoded">
          <input name="x" value="1"/>
          <input type="submit" name="alt" value="Alt"
                 formaction="http://other.com"
                 formmethod="get"
                 formenctype="multipart/form-data"/>
          <input type="submit" name="normal" value="Normal"/>
        </form>
        """
        soup = BeautifulSoup(html, "lxml")
        form_tag = soup.find("form")
        # Choose the overriding submit
        form_obj = Form(form_tag)
        form_obj.choose_submit("alt")
        # The form tag own attributes should be unchanged
        assert form_tag.get("action") == "http://example.com/default"
        assert form_tag.get("method") == "post"
        assert form_tag.get("enctype") == "application/x-www-form-urlencoded"

    def test_only_chosen_submit_in_data(self):
        """Only the chosen submit button name/value is in the request data;
        other submit buttons are excluded."""
        html = """
        <form method="post" action="http://example.com/post">
          <input name="field" value="val"/>
          <input type="submit" name="save" value="Save"/>
          <input type="submit" name="delete" value="Delete"/>
          <button type="submit" name="preview" value="Preview">P</button>
        </form>
        """
        data = _post_data(html, choose_submit_btn="delete")
        names = [n for n, _ in data]
        assert "save" not in names
        assert "preview" not in names
        assert ("delete", "Delete") in data
        assert ("field", "val") in data


# ===========================================================================
# 5. EXTERNAL FORM-ASSOCIATED CONTROLS
# ===========================================================================

class TestExternalControls:
    """Controls with form= attribute outside the form element participate
    once, in stable document order, without duplication."""

    def test_external_control_participates(self):
        """An input with form=myform outside the form element is included
        in submission."""
        page_html = """
        <html><body>
          <form id="myform" method="post" action="mock://form.com/post">
            <input name="inside" value="1"/>
          </form>
          <input form="myform" name="outside" value="2"/>
        </body></html>
        """
        expected_post = [("inside", "1"), ("outside", "2")]
        browser, url = setup_mock_browser(expected_post=expected_post,
                                          text=page_html)
        browser.open(url)
        browser.select_form("#myform")
        res = browser.submit_selected(btnName=False)
        assert res.status_code == 200 and res.text == 'Success!'

    def test_external_control_document_order(self):
        """External controls appear after internal controls (appended at
        end since they appear after the form tag in the document)."""
        page_html = """
        <html><body>
          <form id="f" method="post" action="mock://form.com/post">
            <input name="a" value="1"/>
            <input name="b" value="2"/>
          </form>
          <input form="f" name="c" value="3"/>
          <textarea form="f" name="d">4</textarea>
        </body></html>
        """
        expected_post = [("a", "1"), ("b", "2"), ("c", "3"), ("d", "4")]
        browser, url = setup_mock_browser(expected_post=expected_post,
                                          text=page_html)
        browser.open(url)
        browser.select_form("#f")
        res = browser.submit_selected(btnName=False)
        assert res.status_code == 200 and res.text == 'Success!'

    def test_external_control_no_duplication_on_repeated_submit(self):
        """Selecting the form and submitting multiple times does not
        duplicate values from external controls."""
        page_html = """
        <html><body>
          <form id="f" method="post" action="mock://form.com/post">
            <input name="x" value="1"/>
          </form>
          <input form="f" name="y" value="2"/>
        </body></html>
        """
        expected_post = [("x", "1"), ("y", "2")]
        browser, url = setup_mock_browser(expected_post=expected_post,
                                          text=page_html)
        browser.open(url)
        # First submission
        browser.select_form("#f")
        res = browser.submit_selected(btnName=False)
        assert res.status_code == 200 and res.text == 'Success!'

        # Re-open same page and submit again
        browser.open(url)
        browser.select_form("#f")
        res = browser.submit_selected(btnName=False)
        assert res.status_code == 200 and res.text == 'Success!'

    def test_external_select_participates(self):
        """A select with form= attribute outside the form is included."""
        page_html = """
        <html><body>
          <form id="f" method="post" action="mock://form.com/post">
            <input name="x" value="1"/>
          </form>
          <select form="f" name="color">
            <option value="red">Red</option>
            <option value="blue" selected>Blue</option>
          </select>
        </body></html>
        """
        expected_post = [("x", "1"), ("color", "blue")]
        browser, url = setup_mock_browser(expected_post=expected_post,
                                          text=page_html)
        browser.open(url)
        browser.select_form("#f")
        res = browser.submit_selected(btnName=False)
        assert res.status_code == 200 and res.text == 'Success!'


# ===========================================================================
# 6. CALLER-PROVIDED DATA
# ===========================================================================

class TestCallerProvidedData:
    """Caller-provided request data and parameters combine correctly
    with extracted form values."""

    def test_caller_post_data_combined_with_form(self):
        """Extra data provided by the caller is prepended to form data."""
        html = """
        <form method="post" action="http://example.com/post">
          <input name="from_form" value="form_val"/>
        </form>
        """
        result = _form_data(html, submit_false=True,
                            data={"extra": "caller_val"})
        data = result["data"]
        assert ("extra", "caller_val") in data
        assert ("from_form", "form_val") in data

    def test_caller_get_params_combined_with_form(self):
        """Extra params provided by the caller are prepended to form params."""
        html = """
        <form method="get" action="http://example.com/search">
          <input name="q" value="test"/>
        </form>
        """
        result = _form_data(html, submit_false=True,
                            params={"extra": "caller_val"})
        params = result["params"]
        assert ("extra", "caller_val") in params
        assert ("q", "test") in params

    def test_caller_data_does_not_replace_form_data(self):
        """Both caller data and form data coexist (no clobbering)."""
        html = """
        <form method="post" action="http://example.com/post">
          <input name="shared" value="from_form"/>
          <input name="unique" value="form_only"/>
        </form>
        """
        result = _form_data(html, submit_false=True,
                            data={"shared": "from_caller"})
        data = result["data"]
        # Both should be present (duplicate keys allowed in form data)
        assert ("shared", "from_caller") in data
        assert ("shared", "from_form") in data
        assert ("unique", "form_only") in data

    def test_caller_files_combined_with_form(self):
        """Caller-provided files dict merges with form file inputs."""
        html = """
        <form method="post" action="http://example.com/post"
              enctype="multipart/form-data">
          <input name="text_field" value="hello"/>
        </form>
        """
        fake_file = ("report.csv", io.BytesIO(b"data"))
        result = _form_data(html, submit_false=True,
                            files={"upload": fake_file})
        assert result["files"]["upload"] == fake_file


# ===========================================================================
# Integration: end-to-end with mock browser
# ===========================================================================

class TestIntegrationEndToEnd:
    """Integration tests combining multiple aspects."""

    def test_disabled_fieldset_with_submit(self):
        """Full flow: disabled fieldset controls excluded, submit works."""
        html = """
        <form method="post" action="mock://form.com/post">
          <fieldset disabled>
            <input name="blocked" value="no"/>
          </fieldset>
          <input name="free" value="yes"/>
          <input type="submit" name="go" value="Go"/>
        </form>
        """
        expected_post = [("free", "yes"), ("go", "Go")]
        browser, url = setup_mock_browser(expected_post=expected_post,
                                          text=html)
        browser.open(url)
        browser.select_form()
        res = browser.submit_selected(btnName="go")
        assert res.status_code == 200 and res.text == 'Success!'

    def test_mixed_disabled_readonly_submit(self):
        """Readonly submitted, disabled omitted, correct submit chosen."""
        html = """
        <form method="post" action="mock://form.com/post">
          <input name="ronly" value="keep" readonly/>
          <input name="off" value="drop" disabled/>
          <input type="submit" name="ok" value="OK"/>
          <input type="submit" name="cancel" value="Cancel"/>
        </form>
        """
        expected_post = [("ronly", "keep"), ("ok", "OK")]
        browser, url = setup_mock_browser(expected_post=expected_post,
                                          text=html)
        browser.open(url)
        browser.select_form()
        res = browser.submit_selected(btnName="ok")
        assert res.status_code == 200 and res.text == 'Success!'


if __name__ == '__main__':
    pytest.main(sys.argv)
