import CodeSearchModel

predicate implementationRow(
  string abstractKey,
  string implementationKey,
  string abstractOwnerKey,
  string implementationOwnerKey,
  string relationKind
) {
  exists(Method abstractMethod, Method implementationMethod |
    projectCallable(abstractMethod) and
    projectCallable(implementationMethod) and
    implementationMethod.getAnOverride() = abstractMethod and
    abstractKey = callableKey(abstractMethod) and
    implementationKey = callableKey(implementationMethod) and
    abstractOwnerKey = typeKey(abstractMethod.getDeclaringType()) and
    implementationOwnerKey = typeKey(implementationMethod.getDeclaringType()) and
    relationKind = "implements_method"
  )
  or
  exists(RefType abstractType, RefType implementationType |
    projectType(abstractType) and
    projectType(implementationType) and
    implementationType.extendsOrImplements(abstractType) and
    abstractKey = typeKey(abstractType) and
    implementationKey = typeKey(implementationType) and
    abstractOwnerKey = "" and
    implementationOwnerKey = "" and
    relationKind = "implements_type"
  )
}

from
  string abstractKey, string implementationKey,
  string abstractOwnerKey, string implementationOwnerKey,
  string relationKind
where
  implementationRow(
    abstractKey, implementationKey,
    abstractOwnerKey, implementationOwnerKey,
    relationKind
  )
select
  abstractKey, implementationKey,
  abstractOwnerKey, implementationOwnerKey,
  relationKind
