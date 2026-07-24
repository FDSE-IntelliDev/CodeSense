import CodeSearchModel

predicate symbolRow(
  string stableKey,
  string name,
  string kind,
  string file,
  int startLine,
  int endLine,
  int startColumn,
  string signature,
  string container,
  string qualifiedName,
  string language
) {
  exists(RefType t |
    projectType(t) and
    stableKey = typeKey(t) and
    name = t.getName() and
    kind = typeKind(t) and
    file = t.getFile().getRelativePath() and
    startLine = t.getLocation().getStartLine() and
    endLine = t.getLocation().getEndLine() and
    startColumn = t.getLocation().getStartColumn() and
    signature = t.getQualifiedName() and
    container = typeContainer(t) and
    qualifiedName = t.getQualifiedName() and
    language = sourceLanguage(t)
  )
  or
  exists(Callable c |
    projectCallable(c) and
    stableKey = callableKey(c) and
    name = c.getName() and
    kind = callableKind(c) and
    file = c.getFile().getRelativePath() and
    startLine = c.getLocation().getStartLine() and
    endLine = c.getLocation().getEndLine() and
    startColumn = c.getLocation().getStartColumn() and
    signature = c.getStringSignature() and
    container = c.getDeclaringType().getQualifiedName() and
    qualifiedName = c.getQualifiedName() and
    language = sourceLanguage(c)
  )
  or
  exists(Field f |
    projectField(f) and
    stableKey = fieldKey(f) and
    name = f.getName() and
    kind = "field" and
    file = f.getFile().getRelativePath() and
    startLine = f.getLocation().getStartLine() and
    endLine = f.getLocation().getEndLine() and
    startColumn = f.getLocation().getStartColumn() and
    signature = f.getType().toString() + " " + f.getName() and
    container = f.getDeclaringType().getQualifiedName() and
    qualifiedName = f.getQualifiedName() and
    language = sourceLanguage(f)
  )
}

from
  string stableKey, string name, string kind, string file,
  int startLine, int endLine, int startColumn,
  string signature, string container, string qualifiedName, string language
where
  symbolRow(
    stableKey, name, kind, file, startLine, endLine, startColumn,
    signature, container, qualifiedName, language
  )
select
  stableKey, name, kind, file, startLine, endLine, startColumn,
  signature, container, qualifiedName, language
