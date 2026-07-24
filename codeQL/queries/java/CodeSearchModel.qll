import java

predicate projectType(RefType t) {
  t.fromSource() and
  t.isSourceDeclaration() and
  not t.isCompilerGenerated() and
  t.getFile().isSourceFile()
}

predicate projectCallable(Callable c) {
  c.fromSource() and
  c.isSourceDeclaration() and
  not c.isCompilerGenerated() and
  c.getFile().isSourceFile()
}

predicate projectField(Field f) {
  f.fromSource() and
  f.getSourceDeclaration() = f and
  not f.isCompilerGenerated() and
  f.getFile().isSourceFile()
}

string typeKey(RefType t) {
  result = "type|" + t.getQualifiedName()
}

string callableKey(Callable c) {
  result =
    "callable|" + c.getDeclaringType().getQualifiedName() + "#" +
    c.getSignature()
}

string fieldKey(Field f) {
  result =
    "field|" + f.getDeclaringType().getQualifiedName() + "#" +
    f.getName()
}

string sourceLanguage(Top element) {
  element.getFile().getExtension() = "kt" and result = "kotlin"
  or
  element.getFile().getExtension() != "kt" and result = "java"
}

string typeKind(RefType t) {
  t instanceof Interface and result = "interface"
  or
  t instanceof EnumType and result = "enum"
  or
  not t instanceof Interface and not t instanceof EnumType and result = "class"
}

string callableKind(Callable c) {
  c instanceof Constructor and result = "constructor"
  or
  not c instanceof Constructor and result = "method"
}

string typeContainer(RefType t) {
  exists(RefType enclosing |
    enclosing = t.getEnclosingType() and
    result = enclosing.getQualifiedName()
  )
  or
  not exists(RefType enclosing | enclosing = t.getEnclosingType()) and
  result = t.getPackage().getName()
}
