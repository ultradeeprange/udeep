# Random numbers for the examples' SYNTHETIC data, the same on every machine and Julia version.

"A small generator of random numbers (a 64-bit linear congruential one), so that an example's SYNTHETIC data does
not depend on the Julia version. It is for making test data, and for nothing that needs good randomness."
mutable struct Stream
    state::UInt64
end

function uniform!(s::Stream)                       # in (0, 1)
    s.state = s.state * 0x5851f42d4c957f2d + 0x14057b7ef767814f
    return (Float64(s.state >> 11) + 0.5) / 9007199254740992.0
end

normal!(s::Stream) = sqrt(-2.0 * log(uniform!(s))) * cos(2.0 * pi * uniform!(s))

"A Poisson count of the given mean (Knuth's method: fine for the means of tens used here)."
function poisson!(s::Stream, mean::Float64)
    limit, k, product = exp(-mean), 0, uniform!(s)
    while product > limit
        k += 1
        product *= uniform!(s)
    end
    return k
end
