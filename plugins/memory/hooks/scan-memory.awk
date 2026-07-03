# scan-memory.awk — pure-awk replacement for the claude-memory-scan binary.
#
# Scans memory cards (Markdown with YAML frontmatter) and emits the SessionStart
# hookSpecificOutput JSON with the <memory-index> additionalContext.
#
# Invocation (from session-start):
#   awk -v nsys=<N> -v sysdir=<dir> -v projdir=<dir> -f scan-memory.awk -- <sys files...> <proj files...>
# where the first nsys ARGV files belong to the System section (already sorted),
# the rest to the Project section. Pass 0 files for an empty section.
#
# Output golden: tests/fixtures/expected.json (Rust src/main.rs retired; awk is
# now the SSoT). Card schema is the stable format written by memory:remember:
#   name / description / status (optional) / metadata.type (optional),
# each a single-line plain scalar. Cards with status other than "active"
# (when present) are excluded; cards missing name or description are skipped.

function reset_fm() { c = 0; in_meta = 0; f_name = ""; f_desc = ""; f_status = ""; f_type = ""; have_fm = 0; done_fm = 0 }

function trim(v) { sub(/^[ \t]+/, "", v); sub(/[ \t]+$/, "", v); return v }

# Resolve a YAML plain/quoted scalar the way serde_yaml does, to byte-parity:
#  - a quoted value keeps its content verbatim (one layer of quotes stripped);
#  - an unquoted (plain) value has any inline comment (whitespace + '#' …) stripped.
function scalar(v) {
    v = trim(v)
    if (v ~ /^".*"$/ || v ~ /^'.*'$/) return substr(v, 2, length(v) - 2)
    sub(/[ \t]+#.*/, "", v)        # YAML inline comment on a plain scalar
    sub(/[ \t]+$/, "", v)
    return v
}

# Finalize the frontmatter of the file just parsed into a record.
function finalize(   keep) {
    if (done_fm) return
    done_fm = 1
    if (!have_fm) return                     # no frontmatter at all → skip
    if (f_name == "") return                 # missing required field → skip
    if (f_desc == "") return                 # missing required field → skip
    if (f_status != "" && f_status != "active") return   # inactive → exclude

    keep = ++nrec
    rec_section[keep] = cur_section
    rec_type[keep]    = f_type
    rec_name[keep]    = f_name
    rec_desc[keep]    = f_desc
}

FNR == 1 {
    if (fileno > 0) finalize()               # close the previous file
    fileno++
    cur_section = (fileno <= nsys) ? "System" : "Project"
    reset_fm()
    if ($0 != "---") { done_fm = 1; next }   # not a frontmatter file → skip
    have_fm = 1
    c = 1
    next
}

{
    if (done_fm) next
    if ($0 == "---") { c++; if (c == 2) { finalize() } ; next }
    if (c != 1) next

    if (in_meta) {
        if ($0 ~ /^[ \t]+type:/) {
            line = $0; sub(/^[ \t]*type:[ \t]*/, "", line)
            f_type = scalar(line)
            in_meta = 0
            next
        }
        if ($0 ~ /^[^ \t]/) in_meta = 0      # dedent → metadata block ended
    }

    if ($0 ~ /^name:[ \t]*/)        { line = $0; sub(/^name:[ \t]*/, "", line);        f_name = scalar(line); next }
    if ($0 ~ /^description:[ \t]*/) { line = $0; sub(/^description:[ \t]*/, "", line); f_desc = scalar(line); next }
    if ($0 ~ /^status:[ \t]*/)      { line = $0; sub(/^status:[ \t]*/, "", line);      f_status = scalar(line); next }
    if ($0 ~ /^metadata:[ \t]*$/)   { in_meta = 1; next }
}

# JSON-escape a string (\, ", control chars, newline, tab).
function jesc(s,   out, i, ch, code) {
    gsub(/\\/, "\\\\", s)
    gsub(/"/, "\\\"", s)
    gsub(/\n/, "\\n", s)
    gsub(/\t/, "\\t", s)
    gsub(/\r/, "\\r", s)
    return s
}

# Append a section (System/Project) to the index body if it has records.
function emit_section(label, section,   i, t, ntyped, k, header_done) {
    # any record in this section?
    has = 0
    for (i = 1; i <= nrec; i++) if (rec_section[i] == section) { has = 1; break }
    if (!has) return ""

    body = "## " label

    # untyped first, in scan order
    for (i = 1; i <= nrec; i++)
        if (rec_section[i] == section && rec_type[i] == "")
            body = body "\n- " rec_name[i] " — " rec_desc[i]

    # collect distinct types, sort ascending (BTreeMap parity)
    ntyped = 0
    delete types
    for (i = 1; i <= nrec; i++)
        if (rec_section[i] == section && rec_type[i] != "" && !(rec_type[i] in seen_t)) {
            seen_t[rec_type[i]] = 1
            types[++ntyped] = rec_type[i]
        }
    delete seen_t
    # insertion sort on types[]
    for (i = 2; i <= ntyped; i++) {
        k = types[i]; j = i - 1
        while (j >= 1 && types[j] > k) { types[j+1] = types[j]; j-- }
        types[j+1] = k
    }
    for (t = 1; t <= ntyped; t++) {
        body = body "\n### " types[t]
        for (i = 1; i <= nrec; i++)
            if (rec_section[i] == section && rec_type[i] == types[t])
                body = body "\n- " rec_name[i] " — " rec_desc[i]
    }
    return body
}

END {
    if (fileno > 0) finalize()               # close the last file

    nsysrec = 0; nprojrec = 0
    for (i = 1; i <= nrec; i++) {
        if (rec_section[i] == "System") nsysrec++
        else nprojrec++
    }
    if (nsysrec == 0 && nprojrec == 0) exit 0   # nothing → no output (parity)

    sys = emit_section("System", "System")
    proj = emit_section("Project", "Project")

    content = sys
    if (sys != "" && proj != "") content = sys "\n\n" proj
    else if (proj != "") content = proj

    total = nsysrec + nprojrec
    sysd = (sysdir != "") ? sysdir : "(not set)"
    projd = (projdir != "") ? projdir : "(not set)"

    # Header first: Total / dirs / read-full-file hint lead the block so they
    # survive a host-side truncation of the injected additionalContext (Claude
    # Code persists large SessionStart hook output and injects only a ~2KB
    # preview from the top; footer metadata placed last would be cut off).
    ac = "<memory-index>\n" \
         "Total: " total " cards (" nsysrec " system + " nprojrec " project) · 索引仅 L1 描述，需全文(事实/证据/验证步骤)直接读对应卡文件\n" \
         "dirs: system=" sysd " · project=" projd "\n\n" \
         content "\n</memory-index>"

    printf "{\"hookSpecificOutput\":{\"hookEventName\":\"SessionStart\",\"additionalContext\":\"%s\"}}\n", jesc(ac)
}
