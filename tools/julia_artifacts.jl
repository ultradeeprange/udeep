# Write julia/UDeepCore/Artifacts.toml from the core archives of a release.
#
#     julia tools/julia_artifacts.jl DIRECTORY BASE_URL
#
# DIRECTORY holds the archives tools/core_tarball.py makes (udeep_core-<version>-<platform>.tar.gz), and BASE_URL
# is the public address they are served from, such as
# https://github.com/ultradeeprange/udeep/releases/download/v0.3.0. Each archive is unpacked to find the hash Julia
# names an artifact by, and hashed as a file for the download to be checked against. With the file written, the
# Julia package finds the core without a checkout of this repository or a Rust toolchain: Pkg downloads the one
# archive for the machine when the package is installed.
#
# Run it only for archives that are already at BASE_URL: the file it writes is a promise that they are.
using Pkg
using Pkg.Artifacts
using Base.BinaryPlatforms
using SHA

const PLATFORMS = Dict("x86_64-linux-gnu" => Platform("x86_64", "linux"; libc = "glibc"),
                       "x86_64-w64-mingw32" => Platform("x86_64", "windows"),
                       "aarch64-apple-darwin" => Platform("aarch64", "macos"),
                       "x86_64-apple-darwin" => Platform("x86_64", "macos"))

function main(directory, base_url)
    toml = normpath(joinpath(@__DIR__, "..", "julia", "UDeepCore", "Artifacts.toml"))
    archives = sort(filter(f -> startswith(f, "udeep_core-") && endswith(f, ".tar.gz"), readdir(directory)))
    isempty(archives) && error("no udeep_core-*.tar.gz in $directory")
    rm(toml; force = true)
    for archive in archives
        triple = first(t for t in keys(PLATFORMS) if endswith(archive, "-$t.tar.gz"))
        path = joinpath(directory, archive)
        tree = create_artifact(dir -> Pkg.PlatformEngines.unpack(path, dir))
        library = only(readdir(joinpath(artifact_path(tree), "lib")))
        bind_artifact!(toml, "udeep_core", tree; platform = PLATFORMS[triple], force = true,
                       download_info = [("$(rstrip(base_url, '/'))/$archive", bytes2hex(open(sha256, path)))])
        println(rpad(triple, 22), library, "  ", bytes2hex(tree.bytes))
    end
    println("wrote ", toml, " for ", length(archives), " platforms")
end

length(ARGS) == 2 || error("usage: julia tools/julia_artifacts.jl DIRECTORY BASE_URL")
main(ARGS...)
