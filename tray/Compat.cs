// Required for C# 9.0+ record types when targeting .NET Framework 4.8.
// The init accessor modifier needs this class; .NET 5+ provides it automatically
// but .NET Framework does not, so we supply it here.
namespace System.Runtime.CompilerServices
{
    internal static class IsExternalInit { }
}
