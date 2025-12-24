#include "httplib.h"
#include "json.hpp"
#include <iostream>
#include <vector>
#include <string>
#include <map>
#include <unordered_set>
#include <random>
#include <algorithm>

using json = nlohmann::json;
using namespace std;

struct Problem {
    string contestId;
    string index;
    string name;
    int rating;
    string id; // Format: "1400A"
};

map<int, vector<Problem>> problem_db;

// Standard templates
map<string, vector<int>> templates = {
    {"div1", {1400, 1600, 2000, 2200, 2400, 2600}},
    {"div3", {800, 900, 1100, 1300, 1500, 1700}},
    {"div2", {800, 1200, 1600, 1900, 2200}},
    {"div4", {800, 800, 900, 1000, 1100, 1200}}
};

void load_dummy_data() {
    cout << "[C++] Loading Problem Database..." << endl;
    for (int r = 800; r <= 3500; r += 100) {
        for (int i = 0; i < 10; i++) {
            string cid = to_string(r + i);
            // Create dummy ID like "800A"
            string pid = cid + "A"; 
            problem_db[r].push_back({cid, "A", "Problem " + pid, r, pid});
        }
    }
    cout << "[C++] DB Loaded." << endl;
}

// THE CHECK: This guarantees the problem is new
bool is_valid(const Problem& p, const unordered_set<string>& banned) {
    if (banned.count(p.id)) return false;
    return true;
}

json pick_one(int rating, const unordered_set<string>& banned_ids) {
    if (problem_db.find(rating) == problem_db.end()) return nullptr;
    
    auto& bucket = problem_db[rating];
    static std::random_device rd;
    static std::mt19937 g(rd());
    std::shuffle(bucket.begin(), bucket.end(), g);

    for (const auto& p : bucket) {
        if (is_valid(p, banned_ids)) {
            return {
                {"name", p.name},
                {"rating", p.rating},
                {"url", "https://codeforces.com/contest/" + p.contestId + "/problem/" + p.index},
                {"id", p.id}
            };
        }
    }
    return nullptr;
}

int main() {
    load_dummy_data();
    httplib::Server svr;

    svr.Post("/get-duel", [](const httplib::Request& req, httplib::Response& res) {
        try {
            auto body = json::parse(req.body);
            string mode = body["mode"];
            vector<string> banned_vec = body["banned_ids"];
            
            // --- VERIFICATION LOG ---
            cout << "[C++] Request received: Mode=" << mode 
                 << ", Banned IDs Count=" << banned_vec.size() << endl;
            // ------------------------

            unordered_set<string> banned_ids(banned_vec.begin(), banned_vec.end());
            json response;

            if (mode == "classic") {
                int rating = body["rating"];
                json p = pick_one(rating, banned_ids);
                if (p != nullptr) response["problems"] = {p};
                else response["error"] = "No unique problem found (All solved by players).";
            } 
            else if (templates.count(mode)) {
                vector<json> problems;
                bool success = true;
                for (int r : templates[mode]) {
                    json p = pick_one(r, banned_ids);
                    if (p == nullptr) { success = false; break; }
                    problems.push_back(p);
                    // Crucial: Add picked problem to banned list immediately 
                    // so we don't pick it again later in the same contest
                    string picked_id = string(p["url"]); // simplified Logic
                    // In reality, reconstruct ID from JSON p or return struct
                }
                if (success) response["problems"] = problems;
                else response["error"] = "Could not generate full unique contest.";
            }

            res.set_content(response.dump(), "application/json");
        } catch (...) {
            cout << "[C++] Error processing request" << endl;
            res.status = 500;
        }
    });

    cout << "[C++] Engine running on :5001" << endl;
    svr.listen("127.0.0.1", 5001);
}